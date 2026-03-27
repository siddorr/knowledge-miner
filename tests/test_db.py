from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select

import knowledge_miner.db as db_module
from knowledge_miner.db import (
    Base,
    SessionLocal,
    create_sqlite_backup,
    database_readiness,
    engine,
    ensure_sqlite_schema_compatibility,
    list_sqlite_backup_candidates,
    prune_sqlite_backups,
    repair_sqlite_runtime_data,
    restore_sqlite_backup,
)
from knowledge_miner.config import settings
from knowledge_miner.discovery import create_run
from knowledge_miner.models import CitationExpansionParent, DiscoveryRunQuery, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_database_readiness_and_sqlite_compatibility_restore_feature_tables():
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS paper_annotations")
        conn.exec_driver_sql("DROP TABLE IF EXISTS discovery_citation_seeds")
        conn.exec_driver_sql("DROP TABLE IF EXISTS bookmarks")
        conn.exec_driver_sql("DROP TABLE IF EXISTS session_summary_settings")
        conn.exec_driver_sql("DROP TABLE IF EXISTS session_tag_catalog")

    before = database_readiness()
    assert before["ready"] is False
    assert "bookmarks" in before["missing_tables"]
    assert "paper_annotations" in before["missing_tables"]

    ensure_sqlite_schema_compatibility()

    after = database_readiness()
    assert after["ready"] is True
    assert after["missing_tables"] == []


def test_repair_sqlite_runtime_data_backfills_query_counters_and_supersedes_old_queued_runs():
    with SessionLocal() as db:
        stale = create_run(db, ["upw"], max_iterations=1, session_id="session_repair", session_context="ctx")
        latest = create_run(db, ["semi"], max_iterations=1, session_id="session_repair", session_context="ctx")
        latest.status = "completed"
        stale.status = "queued"

        query = DiscoveryRunQuery(
            id="query_repair_counts",
            run_id=latest.id,
            query_text="citation expansion",
            query_metadata={},
            position=99,
            status="completed",
            discovered_count=12,
            accepted_count=0,
            rejected_count=0,
            pending_count=0,
            processing_count=4,
        )
        db.add(query)
        db.add_all(
            [
                Source(
                    id="src_repair_accept",
                    run_id=latest.id,
                    title="Accepted paper",
                    year=2021,
                    url="https://example.org/accept",
                    doi=None,
                    abstract="accept",
                    journal="j",
                    authors=[],
                    citation_count=1,
                    type="academic",
                    source="openalex",
                    source_native_id="oa_accept",
                    patent_office=None,
                    patent_number=None,
                    iteration=1,
                    discovery_method="forward_citation",
                    relevance_score=5,
                    accepted=True,
                    review_status="human_accept",
                    final_decision="human_accept",
                    decision_source="human_review",
                    heuristic_recommendation="needs_review",
                    heuristic_score=5,
                    ai_decision=None,
                    ai_confidence=None,
                    query_id=query.id,
                    query_step_number=99,
                    query_source_number=1,
                    parent_source_id=None,
                    provenance_history=[],
                ),
                Source(
                    id="src_repair_reject",
                    run_id=latest.id,
                    title="Rejected paper",
                    year=2021,
                    url="https://example.org/reject",
                    doi=None,
                    abstract="reject",
                    journal="j",
                    authors=[],
                    citation_count=1,
                    type="academic",
                    source="openalex",
                    source_native_id="oa_reject",
                    patent_office=None,
                    patent_number=None,
                    iteration=1,
                    discovery_method="forward_citation",
                    relevance_score=1,
                    accepted=False,
                    review_status="auto_reject",
                    final_decision="auto_reject",
                    decision_source="ai",
                    heuristic_recommendation="auto_reject",
                    heuristic_score=1,
                    ai_decision="auto_reject",
                    ai_confidence=0.2,
                    query_id=query.id,
                    query_step_number=99,
                    query_source_number=2,
                    parent_source_id=None,
                    provenance_history=[],
                ),
            ]
        )
        db.add(
            CitationExpansionParent(
                run_id=latest.id,
                parent_source_id="src_repair_accept",
                query_id=query.id,
            )
        )
        db.commit()

        stale_id = stale.id
        latest_id = latest.id

    result = repair_sqlite_runtime_data()
    assert result["repaired_query_rows"] >= 1
    assert result["repaired_run_context_keys"] >= 0
    assert result["superseded_runs"] == 1

    with SessionLocal() as db:
        refreshed_query = db.get(DiscoveryRunQuery, "query_repair_counts")
        assert refreshed_query is not None
        assert refreshed_query.accepted_count == 1
        assert refreshed_query.rejected_count == 1
        assert refreshed_query.pending_count == 0
        assert refreshed_query.processing_count == 0

        stale_run = db.get(Run, stale_id)
        latest_run = db.get(Run, latest_id)
        repaired_parent = db.get(CitationExpansionParent, (latest_id, "src_repair_accept"))
        assert stale_run is not None
        assert latest_run is not None
        assert repaired_parent is not None
        assert stale_run.session_context_key
        assert latest_run.session_context_key
        assert repaired_parent.session_id == latest_run.session_id
        assert repaired_parent.session_context_key == latest_run.session_context_key
        assert stale_run.status == "failed"
        assert stale_run.error_message == "superseded_stale_queued_run"
        assert latest_run.status == "completed"

        stale_queries = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == stale_id)).all()
        assert stale_queries
        assert all(row.status == "failed" for row in stale_queries)


def test_sqlite_backup_helpers_list_and_restore(tmp_path, monkeypatch):
    live_db = tmp_path / "knowledge_miner.db"
    backup_dir = tmp_path / "db_backups"
    backup_old = tmp_path / "knowledge_miner.db.bak.20260324_010101"
    backup_new = tmp_path / "knowledge_miner.db.before_restore_manual"
    for target, marker in ((live_db, "live"), (backup_old, "old"), (backup_new, "new")):
        with sqlite3.connect(target) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker (value) VALUES (?)", (marker,))
            conn.commit()
    backup_dir.mkdir()
    managed_auto = backup_dir / "knowledge_miner.db.auto_20260325_010101"
    with sqlite3.connect(managed_auto) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker (value) VALUES ('auto')")
        conn.commit()
    (tmp_path / "knowledge_miner.db-wal").write_text("wal", encoding="utf-8")
    (tmp_path / "knowledge_miner.db-shm").write_text("shm", encoding="utf-8")

    url = f"sqlite:///{live_db}"
    original_backup_dir = settings.db_backup_dir
    object.__setattr__(settings, "db_backup_dir", str(backup_dir))
    try:
        items = list_sqlite_backup_candidates(url)
        assert items[0]["name"] == managed_auto.name
        assert items[0]["kind"] == "auto"
        assert items[0]["managed"] is True
        legacy_names = {item["name"] for item in items[1:]}
        assert legacy_names == {backup_new.name, backup_old.name}
        assert all(item["kind"] == "legacy" for item in items[1:])

        monkeypatch.setattr(db_module, "ensure_sqlite_schema_compatibility", lambda: None)
        monkeypatch.setattr(
            db_module,
            "repair_sqlite_runtime_data",
            lambda: {"repaired_query_rows": 0, "superseded_runs": 0, "superseded_query_rows": 0},
        )
        result = restore_sqlite_backup(backup_new.name, database_url=url)
        assert result["restored_backup_name"] == backup_new.name
        assert Path(result["snapshot_path"]).exists()
        with sqlite3.connect(live_db) as conn:
            restored_marker = conn.execute("SELECT value FROM marker").fetchone()[0]
        assert restored_marker == "new"
        assert Path(result["snapshot_path"]).parent == backup_dir
        assert not (tmp_path / "knowledge_miner.db-wal").exists()
        assert not (tmp_path / "knowledge_miner.db-shm").exists()
    finally:
        object.__setattr__(settings, "db_backup_dir", original_backup_dir)


def test_sqlite_backup_helpers_create_and_prune_auto_backups(tmp_path):
    live_db = tmp_path / "knowledge_miner.db"
    with sqlite3.connect(live_db) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker (value) VALUES ('live')")
        conn.commit()
    backup_dir = tmp_path / "db_backups"
    original_backup_dir = settings.db_backup_dir
    original_retention = settings.db_backup_retention_count
    object.__setattr__(settings, "db_backup_dir", str(backup_dir))
    object.__setattr__(settings, "db_backup_retention_count", 2)
    try:
        url = f"sqlite:///{live_db}"
        manual = create_sqlite_backup("manual", database_url=url)
        assert manual["kind"] == "manual"
        assert Path(manual["path"]).exists()

        for suffix in ("20260325_000001", "20260325_000002", "20260325_000003"):
            path = backup_dir / f"knowledge_miner.db.auto_{suffix}"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE marker (value TEXT)")
                conn.execute("INSERT INTO marker (value) VALUES ('auto')")
                conn.commit()

        result = prune_sqlite_backups(database_url=url, retention_count=2)
        assert result["pruned_auto_backups"] == 1
        auto_names = [row["name"] for row in list_sqlite_backup_candidates(url) if row["kind"] == "auto"]
        assert len(auto_names) == 2
        assert Path(manual["path"]).exists()
    finally:
        object.__setattr__(settings, "db_backup_dir", original_backup_dir)
        object.__setattr__(settings, "db_backup_retention_count", original_retention)
