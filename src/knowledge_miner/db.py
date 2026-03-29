from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
import shutil
from threading import Lock
from typing import Literal
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


logger = logging.getLogger("knowledge_miner")


_ENGINE_KWARGS: dict = {"future": True}
if settings.database_url.lower().startswith("sqlite:///"):
    _ENGINE_KWARGS["connect_args"] = {"timeout": 30}
engine = create_engine(settings.database_url, **_ENGINE_KWARGS)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
_SQLITE_RESTORE_LOCK = Lock()
REQUIRED_TABLES = (
    "runs",
    "sources",
    "discovery_run_queries",
    "citation_expansion_parents",
    "discovery_citation_seeds",
    "acquisition_runs",
    "parse_runs",
    "session_profiles",
    "session_tag_catalog",
    "session_tag_specs",
    "session_summary_settings",
    "bookmarks",
    "paper_annotations",
)


if settings.database_url.lower().startswith("sqlite:///"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout = 30000")
        finally:
            cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sqlite_file_path(database_url: str) -> str | None:
    raw = database_url.strip()
    if not raw.lower().startswith("sqlite:///"):
        return None
    if raw.startswith("sqlite:///:memory:"):
        return ":memory:"
    return str(Path(raw[len("sqlite:///") :]).resolve())


def sqlite_file_metadata(database_url: str) -> dict:
    path = sqlite_file_path(database_url)
    if not path or path == ":memory:":
        return {"path": path, "inode": None, "mtime": None}
    target = Path(path)
    if not target.exists():
        return {"path": path, "inode": None, "mtime": None}
    stat = target.stat()
    return {"path": path, "inode": int(stat.st_ino), "mtime": int(stat.st_mtime)}


def normalize_session_context(value: str | None) -> str:
    return " ".join((value or "").split())


def session_context_key(value: str | None) -> str:
    normalized = normalize_session_context(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sqlite_target_path(database_url: str | None = None) -> Path | None:
    target_url = database_url or settings.database_url
    path = sqlite_file_path(target_url)
    if not path or path == ":memory:":
        return None
    return Path(path).resolve()


def _sqlite_backup_supported(database_url: str | None = None) -> bool:
    live_db = _sqlite_target_path(database_url)
    if live_db is None:
        return False
    return live_db.name != "test_knowledge_miner.db"


def sqlite_backup_dir(database_url: str | None = None) -> Path | None:
    if not _sqlite_backup_supported(database_url):
        return None
    return Path(settings.db_backup_dir).resolve()


def ensure_sqlite_backup_dir(database_url: str | None = None) -> Path | None:
    backup_dir = sqlite_backup_dir(database_url)
    if backup_dir is None:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def classify_sqlite_backup_name(name: str) -> tuple[str, bool]:
    if ".auto_" in name:
        return ("auto", True)
    if ".manual_" in name:
        return ("manual", True)
    if ".pre_restore_" in name:
        return ("pre_restore", True)
    return ("legacy", False)


def _backup_row_for(candidate: Path) -> dict:
    stat = candidate.stat()
    kind, managed = classify_sqlite_backup_name(candidate.name)
    return {
        "name": candidate.name,
        "path": str(candidate.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime": int(stat.st_mtime),
        "kind": kind,
        "managed": managed,
    }


def list_sqlite_backup_candidates(database_url: str | None = None) -> list[dict]:
    live_db = _sqlite_target_path(database_url)
    if live_db is None:
        return []
    if not live_db.exists():
        return []
    rows: list[dict] = []
    backup_dir = sqlite_backup_dir(database_url)
    if backup_dir is not None and backup_dir.exists():
        for candidate in backup_dir.iterdir():
            if not candidate.is_file():
                continue
            if not candidate.name.startswith(f"{live_db.name}."):
                continue
            rows.append(_backup_row_for(candidate))
    prefix = f"{live_db.name}."
    for candidate in live_db.parent.iterdir():
        if not candidate.is_file() or candidate.resolve() == live_db.resolve():
            continue
        if not candidate.name.startswith(prefix):
            continue
        if backup_dir is not None and candidate.resolve().parent == backup_dir.resolve():
            continue
        rows.append(_backup_row_for(candidate))
    rows.sort(key=lambda item: (0 if item["managed"] else 1, -item["mtime"], item["name"]))
    return rows


def _resolve_sqlite_backup_candidate(backup_name: str, database_url: str | None = None) -> Path:
    live_db = _sqlite_target_path(database_url)
    if live_db is None:
        raise ValueError("sqlite_database_restore_not_supported")
    candidate_paths = []
    backup_dir = sqlite_backup_dir(database_url)
    if backup_dir is not None:
        candidate_paths.append((backup_dir / backup_name).resolve())
    candidate_paths.append((live_db.parent / backup_name).resolve())
    for selected in candidate_paths:
        if selected == live_db:
            raise ValueError("sqlite_database_restore_source_must_be_backup")
        if selected.parent not in {live_db.parent, backup_dir.resolve() if backup_dir is not None else live_db.parent}:
            continue
        if selected.is_file() and selected.name.startswith(f"{live_db.name}."):
            return selected
    raise ValueError("sqlite_database_restore_backup_not_found")


def create_sqlite_backup(
    kind: Literal["auto", "manual", "pre_restore"],
    *,
    database_url: str | None = None,
) -> dict:
    live_db = _sqlite_target_path(database_url)
    if live_db is None:
        raise ValueError("sqlite_database_backup_not_supported")
    if live_db.name == "test_knowledge_miner.db":
        raise ValueError("sqlite_database_backup_disabled_for_test_db")
    if not live_db.exists():
        raise ValueError("sqlite_database_backup_source_missing")
    backup_dir = ensure_sqlite_backup_dir(database_url)
    if backup_dir is None:
        raise ValueError("sqlite_database_backup_not_supported")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{live_db.name}.{kind}_{timestamp}"
    with _SQLITE_RESTORE_LOCK:
        engine.dispose()
        shutil.copy2(live_db, backup_path)
    return _backup_row_for(backup_path)


def prune_sqlite_backups(
    *,
    database_url: str | None = None,
    retention_count: int | None = None,
) -> dict[str, int]:
    backup_dir = sqlite_backup_dir(database_url)
    if backup_dir is None or not backup_dir.exists():
        return {"pruned_auto_backups": 0}
    keep = max(0, int(retention_count if retention_count is not None else settings.db_backup_retention_count))
    auto_rows = sorted(
        [row for row in list_sqlite_backup_candidates(database_url) if row["managed"] and row["kind"] == "auto"],
        key=lambda item: (item["mtime"], item["name"]),
        reverse=True,
    )
    pruned = 0
    for row in auto_rows[keep:]:
        path = Path(row["path"])
        if path.exists():
            path.unlink()
            pruned += 1
    return {"pruned_auto_backups": pruned}


def restore_sqlite_backup(backup_name: str, *, database_url: str | None = None) -> dict:
    target_url = database_url or settings.database_url
    live_db = _sqlite_target_path(target_url)
    if live_db is None:
        raise ValueError("sqlite_database_restore_not_supported")
    backup_path = _resolve_sqlite_backup_candidate(backup_name, target_url)
    snapshot = create_sqlite_backup("pre_restore", database_url=target_url)
    snapshot_path = Path(snapshot["path"])
    with _SQLITE_RESTORE_LOCK:
        engine.dispose()
        shutil.copy2(backup_path, live_db)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{live_db}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        ensure_sqlite_schema_compatibility()
        repair_result = repair_sqlite_runtime_data()
    meta = sqlite_file_metadata(target_url)
    return {
        "restored_from": str(backup_path),
        "restored_backup_name": backup_path.name,
        "snapshot_path": str(snapshot_path),
        "database_target": str(live_db),
        "database_inode": meta["inode"],
        "database_mtime": meta["mtime"],
        "snapshot_kind": snapshot["kind"],
        **repair_result,
    }


def database_readiness() -> dict:
    file_meta = sqlite_file_metadata(settings.database_url)
    try:
        names = set(inspect(engine).get_table_names())
    except SQLAlchemyError as exc:
        return {
            "ready": False,
            "missing_tables": list(REQUIRED_TABLES),
            "error": f"{exc.__class__.__name__}: {exc}",
            "required_tables": list(REQUIRED_TABLES),
            "database_url": settings.database_url,
            "sqlite_file_path": file_meta["path"],
            "sqlite_file_inode": file_meta["inode"],
            "sqlite_file_mtime": file_meta["mtime"],
        }
    missing = [name for name in REQUIRED_TABLES if name not in names]
    return {
        "ready": not missing,
        "missing_tables": missing,
        "error": None if not missing else "schema_missing",
        "required_tables": list(REQUIRED_TABLES),
        "database_url": settings.database_url,
        "sqlite_file_path": file_meta["path"],
        "sqlite_file_inode": file_meta["inode"],
        "sqlite_file_mtime": file_meta["mtime"],
    }


def ensure_sqlite_schema_compatibility() -> None:
    """Apply lightweight additive SQLite migrations for local/dev continuity."""
    if not settings.database_url.lower().startswith("sqlite:///"):
        return
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)
        table_names = set(inspect(conn).get_table_names())

        if "runs" in table_names:
            run_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(runs)").fetchall()}
            if "session_id" not in run_columns:
                conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN session_id VARCHAR")
            if "session_context" not in run_columns:
                conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN session_context TEXT")
            if "session_context_key" not in run_columns:
                conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN session_context_key VARCHAR")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_runs_session_context_key ON runs (session_context_key)")

        if "discovery_run_queries" in table_names:
            query_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(discovery_run_queries)").fetchall()
            }
            if "query_metadata" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN query_metadata JSON")
                conn.execute(text("UPDATE discovery_run_queries SET query_metadata = '{}' WHERE query_metadata IS NULL"))
            if "openalex_status" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN openalex_status VARCHAR NOT NULL DEFAULT 'pending'")
            if "semantic_scholar_status" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN semantic_scholar_status VARCHAR NOT NULL DEFAULT 'pending'")
            if "brave_status" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN brave_status VARCHAR NOT NULL DEFAULT 'pending'")
            if "openalex_error_message" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN openalex_error_message TEXT")
            if "semantic_scholar_error_message" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN semantic_scholar_error_message TEXT")
            if "brave_error_message" not in query_columns:
                conn.exec_driver_sql("ALTER TABLE discovery_run_queries ADD COLUMN brave_error_message TEXT")

        if "sources" in table_names:
            source_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sources)").fetchall()}
            if "query_id" not in source_columns:
                conn.exec_driver_sql("ALTER TABLE sources ADD COLUMN query_id VARCHAR")
            if "query_step_number" not in source_columns:
                conn.exec_driver_sql("ALTER TABLE sources ADD COLUMN query_step_number INTEGER")
            if "query_source_number" not in source_columns:
                conn.exec_driver_sql("ALTER TABLE sources ADD COLUMN query_source_number INTEGER")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sources_query_id ON sources (query_id)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sources_run_query_lineage ON sources (run_id, query_id, query_source_number)"
            )

        if "citation_expansion_parents" in table_names:
            citation_parent_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(citation_expansion_parents)").fetchall()
            }
            if "session_id" not in citation_parent_columns:
                conn.exec_driver_sql("ALTER TABLE citation_expansion_parents ADD COLUMN session_id VARCHAR")
            if "session_context_key" not in citation_parent_columns:
                conn.exec_driver_sql("ALTER TABLE citation_expansion_parents ADD COLUMN session_context_key VARCHAR")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_citation_expansion_parents_session_context_parent ON "
                "citation_expansion_parents (session_id, session_context_key, parent_source_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_citation_expansion_parents_session_context_expanded_at ON "
                "citation_expansion_parents (session_id, session_context_key, expanded_at)"
            )

        if "paper_annotations" in table_names:
            annotation_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(paper_annotations)").fetchall()
            }
            if "ai_suggested_tags_json" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN ai_suggested_tags_json JSON")
                conn.execute(text("UPDATE paper_annotations SET ai_suggested_tags_json = '[]' WHERE ai_suggested_tags_json IS NULL"))
            if "ai_summary_json" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN ai_summary_json JSON")
            if "summary_editor_snapshot_json" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN summary_editor_snapshot_json JSON")
            if "tag_suggestion_status" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN tag_suggestion_status VARCHAR")
                conn.execute(text("UPDATE paper_annotations SET tag_suggestion_status = 'none' WHERE tag_suggestion_status IS NULL"))
            if "tag_suggestion_prompt_snapshot" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN tag_suggestion_prompt_snapshot TEXT")
            if "tag_suggestion_model" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN tag_suggestion_model VARCHAR")
            if "tag_suggestion_generated_at" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN tag_suggestion_generated_at DATETIME")
            if "tag_suggestion_error" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN tag_suggestion_error TEXT")
            if "freeform_tags_by_category_json" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN freeform_tags_by_category_json JSON")
            if "approved_tags_by_category_json" not in annotation_columns:
                conn.exec_driver_sql("ALTER TABLE paper_annotations ADD COLUMN approved_tags_by_category_json JSON")

        if "session_profiles" in table_names:
            session_profile_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(session_profiles)").fetchall()
            }
            if "tag_candidate_status" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_candidate_status VARCHAR")
                conn.execute(text("UPDATE session_profiles SET tag_candidate_status = 'none' WHERE tag_candidate_status IS NULL"))
            if "tag_candidate_generated_at" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_candidate_generated_at DATETIME")
            if "tag_candidate_error" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_candidate_error TEXT")
            if "tag_assignment_status" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_assignment_status VARCHAR")
                conn.execute(text("UPDATE session_profiles SET tag_assignment_status = 'none' WHERE tag_assignment_status IS NULL"))
            if "tag_assignment_generated_at" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_assignment_generated_at DATETIME")
            if "tag_assignment_error" not in session_profile_columns:
                conn.exec_driver_sql("ALTER TABLE session_profiles ADD COLUMN tag_assignment_error TEXT")

        if "session_tag_catalog" in table_names:
            tag_catalog_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(session_tag_catalog)").fetchall()
            }
            if "category_key" not in tag_catalog_columns:
                conn.exec_driver_sql(
                    "ALTER TABLE session_tag_catalog ADD COLUMN category_key VARCHAR NOT NULL DEFAULT 'uncategorized_tags'"
                )
                conn.execute(
                    text(
                        "UPDATE session_tag_catalog SET category_key = 'uncategorized_tags' "
                        "WHERE category_key IS NULL OR TRIM(category_key) = ''"
                    )
                )
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_session_tag_catalog_session_id_tag")
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_session_tag_catalog_session_id_category_tag "
                "ON session_tag_catalog (session_id, category_key, tag)"
            )

        if "session_tag_candidates" not in table_names:
            conn.exec_driver_sql(
                """
                CREATE TABLE session_tag_candidates (
                    id VARCHAR PRIMARY KEY,
                    session_id VARCHAR NOT NULL,
                    category_key VARCHAR NOT NULL DEFAULT 'uncategorized_tags',
                    tag VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'candidate',
                    source_count INTEGER NOT NULL DEFAULT 0,
                    source_ids_json JSON NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_session_tag_candidates_session_id_category_tag "
                "ON session_tag_candidates (session_id, category_key, tag)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_session_tag_candidates_session_id_category_status_updated_at "
                "ON session_tag_candidates (session_id, category_key, status, updated_at)"
            )
        else:
            tag_candidate_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(session_tag_candidates)").fetchall()
            }
            if "category_key" not in tag_candidate_columns:
                conn.exec_driver_sql(
                    "ALTER TABLE session_tag_candidates ADD COLUMN category_key VARCHAR NOT NULL DEFAULT 'uncategorized_tags'"
                )
                conn.execute(
                    text(
                        "UPDATE session_tag_candidates SET category_key = 'uncategorized_tags' "
                        "WHERE category_key IS NULL OR TRIM(category_key) = ''"
                    )
                )
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_session_tag_candidates_session_id_tag")
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_session_tag_candidates_session_id_status_updated_at")
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_session_tag_candidates_session_id_category_tag "
                "ON session_tag_candidates (session_id, category_key, tag)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_session_tag_candidates_session_id_category_status_updated_at "
                "ON session_tag_candidates (session_id, category_key, status, updated_at)"
            )

        if "session_tag_specs" not in table_names:
            conn.exec_driver_sql(
                """
                CREATE TABLE session_tag_specs (
                    session_id VARCHAR PRIMARY KEY,
                    category_config_json JSON NOT NULL,
                    prompt_template TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )

        if "session_summary_settings" in table_names:
            summary_settings_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(session_summary_settings)").fetchall()
            }
            if "editor_config_json" not in summary_settings_columns:
                conn.exec_driver_sql("ALTER TABLE session_summary_settings ADD COLUMN editor_config_json JSON")

        if "artifacts" in table_names:
            artifact_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(artifacts)").fetchall()}
            if "quality_status" not in artifact_columns:
                conn.exec_driver_sql("ALTER TABLE artifacts ADD COLUMN quality_status VARCHAR")
            if "quality_reason" not in artifact_columns:
                conn.exec_driver_sql("ALTER TABLE artifacts ADD COLUMN quality_reason VARCHAR")


def repair_sqlite_runtime_data() -> dict[str, int]:
    if not settings.database_url.lower().startswith("sqlite:///"):
        return {
            "repaired_query_rows": 0,
            "repaired_run_context_keys": 0,
            "repaired_citation_parent_scope_rows": 0,
            "superseded_runs": 0,
            "superseded_query_rows": 0,
        }

    from .models import AcquisitionItem, AcquisitionRun, Artifact, CitationExpansionParent, DiscoveryRunQuery, DocumentChunk, PaperAnnotation, ParseRun, ParsedDocument, Run, Source
    from .artifact_quality import classify_html_artifact

    repaired_query_rows = 0
    repaired_run_context_keys = 0
    repaired_citation_parent_scope_rows = 0
    superseded_runs = 0
    superseded_query_rows = 0
    repaired_artifact_quality_rows = 0
    downgraded_invalid_html_items = 0
    upgraded_valid_artifact_items = 0
    invalidated_bad_html_parse_docs = 0
    now = datetime.now(UTC)

    with SessionLocal() as db:
        runs = db.scalars(select(Run).order_by(Run.created_at.asc())).all()
        runs_by_id = {run.id: run for run in runs}
        for run in runs:
            target_key = session_context_key(run.session_context)
            if (run.session_context_key or "") != target_key:
                run.session_context_key = target_key
                run.updated_at = now
                repaired_run_context_keys += 1

        citation_parent_rows = db.scalars(
            select(CitationExpansionParent).order_by(CitationExpansionParent.expanded_at.asc(), CitationExpansionParent.run_id.asc())
        ).all()
        for row in citation_parent_rows:
            run = runs_by_id.get(row.run_id)
            if run is None:
                continue
            target_session_id = (run.session_id or "").strip() or None
            target_context_key = (run.session_context_key or "").strip() or session_context_key(run.session_context)
            if (row.session_id or None) != target_session_id or (row.session_context_key or "") != target_context_key:
                row.session_id = target_session_id
                row.session_context_key = target_context_key
                repaired_citation_parent_scope_rows += 1

        annotation_rows = db.scalars(select(PaperAnnotation).order_by(PaperAnnotation.created_at.asc())).all()
        for row in annotation_rows:
            changed = False
            if row.ai_suggested_tags_json is None:
                row.ai_suggested_tags_json = []
                changed = True
            if not (row.tag_suggestion_status or "").strip():
                row.tag_suggestion_status = "none"
                changed = True
            if changed:
                row.updated_at = now

        artifact_rows = db.scalars(select(Artifact).order_by(Artifact.created_at.asc(), Artifact.id.asc())).all()
        parse_runs_to_refresh: set[str] = set()
        touched_acq_runs: set[str] = set()
        for artifact in artifact_rows:
            target_status = artifact.quality_status
            target_reason = artifact.quality_reason
            if artifact.kind == "pdf":
                target_status = "pdf"
                target_reason = "publisher_pdf"
            elif artifact.kind == "html":
                path = Path(settings.artifacts_dir) / artifact.path
                if path.exists():
                    try:
                        html_text = path.read_text(encoding="utf-8", errors="ignore")
                        quality = classify_html_artifact(html_text=html_text, url=None)
                        target_status = quality.status
                        target_reason = quality.reason
                    except Exception:
                        target_status = "html_invalid"
                        target_reason = "html_missing_article_signals"
                else:
                    target_status = "html_invalid"
                    target_reason = "artifact_file_missing"
            if (artifact.quality_status or None) != (target_status or None) or (artifact.quality_reason or None) != (target_reason or None):
                artifact.quality_status = target_status
                artifact.quality_reason = target_reason
                repaired_artifact_quality_rows += 1
            touched_acq_runs.add(artifact.acq_run_id)
            linked_items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.id == artifact.item_id)).all() if artifact.item_id else []
            for item in linked_items:
                if target_status in {"pdf", "html_validated"} and item.status in {"partial", "failed"}:
                    item.status = "downloaded"
                    item.last_error = None
                    item.reason_code = None
                    item.updated_at = now
                    upgraded_valid_artifact_items += 1
                elif item.status == "downloaded" and target_status == "html_invalid":
                    item.status = "partial"
                    item.last_error = "html_invalid"
                    item.reason_code = "source_error"
                    item.updated_at = now
                    downgraded_invalid_html_items += 1
            if artifact.kind != "html" or target_status != "html_invalid":
                continue
            parse_docs = db.scalars(select(ParsedDocument).where(ParsedDocument.artifact_id == artifact.id)).all()
            for doc in parse_docs:
                if doc.status == "parsed":
                    db.execute(text("DELETE FROM document_chunks WHERE parsed_document_id = :doc_id"), {"doc_id": doc.id})
                    doc.status = "failed"
                    doc.body_text = None
                    doc.language = None
                    doc.parser_used = None
                    doc.char_count = 0
                    doc.section_count = 0
                    doc.content_hash = None
                    doc.relevance_score = None
                    doc.decision = None
                    doc.confidence = None
                    doc.reason = None
                    doc.last_error = f"html_invalid:{target_reason}"
                    doc.updated_at = now
                    parse_runs_to_refresh.add(doc.parse_run_id)
                    invalidated_bad_html_parse_docs += 1

        item_artifact_rows = db.execute(
            select(AcquisitionItem, Artifact)
            .join(Artifact, Artifact.item_id == AcquisitionItem.id)
            .order_by(AcquisitionItem.updated_at.asc(), AcquisitionItem.id.asc())
        ).all()
        for item, artifact in item_artifact_rows:
            touched_acq_runs.add(item.acq_run_id)
            if artifact.quality_status in {"pdf", "html_validated"} and item.status in {"partial", "failed"}:
                item.status = "downloaded"
                item.last_error = None
                item.reason_code = None
                item.updated_at = now
                upgraded_valid_artifact_items += 1
            elif artifact.quality_status == "html_invalid" and item.status == "downloaded":
                item.status = "partial"
                item.last_error = "html_invalid"
                item.reason_code = "source_error"
                item.updated_at = now
                downgraded_invalid_html_items += 1

        for acq_run_id in touched_acq_runs:
            statuses = db.scalars(select(AcquisitionItem.status).where(AcquisitionItem.acq_run_id == acq_run_id)).all()
            acq_run = db.get(AcquisitionRun, acq_run_id)
            if acq_run is None:
                continue
            acq_run.downloaded_total = sum(1 for status in statuses if status == "downloaded")
            acq_run.partial_total = sum(1 for status in statuses if status == "partial")
            acq_run.failed_total = sum(1 for status in statuses if status == "failed")
            acq_run.skipped_total = sum(1 for status in statuses if status == "skipped")
            acq_run.updated_at = now

        for parse_run_id in parse_runs_to_refresh:
            parse_run = db.get(ParseRun, parse_run_id)
            if parse_run is None:
                continue
            parse_run.parsed_total = int(
                db.scalar(select(func.count()).select_from(ParsedDocument).where(ParsedDocument.parse_run_id == parse_run_id, ParsedDocument.status == "parsed")) or 0
            )
            parse_run.failed_total = int(
                db.scalar(select(func.count()).select_from(ParsedDocument).where(ParsedDocument.parse_run_id == parse_run_id, ParsedDocument.status == "failed")) or 0
            )
            parse_run.chunked_total = int(
                db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.parse_run_id == parse_run_id)) or 0
            )
            parse_run.updated_at = now

        query_rows = db.scalars(select(DiscoveryRunQuery).order_by(DiscoveryRunQuery.created_at.asc())).all()
        for query_row in query_rows:
            statuses = db.scalars(
                select(Source.review_status).where(
                    Source.run_id == query_row.run_id,
                    Source.query_id == query_row.id,
                )
            ).all()
            accepted = sum(1 for status in statuses if status in {"auto_accept", "human_accept"})
            rejected = sum(1 for status in statuses if status in {"auto_reject", "human_reject"})
            processing = sum(1 for status in statuses if status == "processing")
            pending = max(len(statuses) - accepted - rejected - processing, 0)
            target_processing = 0 if query_row.status in {"completed", "failed"} else int(query_row.processing_count or 0)
            if (
                int(query_row.accepted_count or 0) != accepted
                or int(query_row.rejected_count or 0) != rejected
                or int(query_row.pending_count or 0) != pending
                or int(query_row.processing_count or 0) != target_processing
            ):
                query_row.accepted_count = accepted
                query_row.rejected_count = rejected
                query_row.pending_count = pending
                query_row.processing_count = target_processing
                query_row.updated_at = now
                repaired_query_rows += 1

            provider_defaults = {
                "openalex": "ok" if int(query_row.openalex_count or 0) > 0 else "empty",
                "semantic_scholar": (
                    "disabled"
                    if not settings.use_semantic_scholar
                    else ("ok" if int(query_row.semantic_scholar_count or 0) > 0 else "empty")
                ),
                "brave": (
                    "disabled"
                    if not settings.brave_api_key
                    else ("ok" if int(query_row.brave_count or 0) > 0 else "empty")
                ),
            }
            if query_row.status in {"waiting", "searching", "ranking_relevance"}:
                for provider_name, enabled in (
                    ("openalex", True),
                    ("semantic_scholar", settings.use_semantic_scholar),
                    ("brave", bool(settings.brave_api_key)),
                ):
                    status_attr = f"{provider_name}_status"
                    current_status = str(getattr(query_row, status_attr, "") or "")
                    if current_status in {"", "empty"}:
                        setattr(query_row, status_attr, "pending" if enabled else "disabled")
                        query_row.updated_at = now
                        repaired_query_rows += 1
            else:
                for provider_name, default_status in provider_defaults.items():
                    status_attr = f"{provider_name}_status"
                    current_status = str(getattr(query_row, status_attr, "") or "")
                    if current_status in {"", "pending", "running"}:
                        setattr(query_row, status_attr, default_status)
                        query_row.updated_at = now
                        repaired_query_rows += 1

        queued_runs = db.scalars(
            select(Run).where(Run.status == "queued").order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
        for run in queued_runs:
            if not (run.session_id or "").strip():
                continue
            newer_run = db.scalars(
                select(Run)
                .where(
                    Run.session_id == run.session_id,
                    Run.id != run.id,
                    Run.created_at > run.created_at,
                    Run.status.in_(("running", "completed")),
                )
                .order_by(Run.created_at.desc(), Run.id.desc())
                .limit(1)
            ).first()
            if newer_run is None:
                continue
            run_query_rows = db.scalars(
                select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id).order_by(DiscoveryRunQuery.position.asc())
            ).all()
            if any((row.status or "").strip() != "waiting" for row in run_query_rows):
                continue
            run.status = "failed"
            run.error_message = "superseded_stale_queued_run"
            run.updated_at = now
            superseded_runs += 1
            for row in run_query_rows:
                row.status = "failed"
                row.error_message = "superseded_stale_queued_run"
                row.processing_count = 0
                row.updated_at = now
                superseded_query_rows += 1

        db.commit()

    return {
        "repaired_query_rows": repaired_query_rows,
        "repaired_run_context_keys": repaired_run_context_keys,
        "repaired_citation_parent_scope_rows": repaired_citation_parent_scope_rows,
        "superseded_runs": superseded_runs,
        "superseded_query_rows": superseded_query_rows,
        "repaired_artifact_quality_rows": repaired_artifact_quality_rows,
        "downgraded_invalid_html_items": downgraded_invalid_html_items,
        "upgraded_valid_artifact_items": upgraded_valid_artifact_items,
        "invalidated_bad_html_parse_docs": invalidated_bad_html_parse_docs,
    }
