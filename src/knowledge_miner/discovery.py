from __future__ import annotations

from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import queue
import time
from pathlib import Path
import threading
from typing import Iterable
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai_filter import AIRelevanceFilter, describe_ai_filter_runtime
from .config import settings
from .connectors import Connector, RetryableProviderError, build_connectors
from .db import SessionLocal, normalize_session_context, session_context_key
from .dedup import canonical_id, canonicalize_url, is_fuzzy_duplicate
from .iteration import build_next_queries, extract_keywords
from .models import Bookmark, CitationEdge, CitationExpansionParent, DiscoveryCitationSeed, DiscoveryRunQuery, Keyword, Run, Source
from .observability import RunObservability
from .retry import retry_call
from .runtime_state import (
    acquire_run_lock,
    clear_run_stop_request,
    is_primary_instance,
    is_run_stop_requested,
    release_run_lock,
)


class RunStopRequested(RuntimeError):
    pass
from .scoring import decision_from_score, score_text

logger = logging.getLogger("knowledge_miner")
HUMAN_REVIEW_STATUSES = {"human_accept", "human_reject", "human_later"}
AI_EVALUATION_WATCHDOG_MAX_SECONDS = 5.0
AI_EVALUATION_WATCHDOG_FLOOR_SECONDS = 1.0
PROVIDER_STATUS_PENDING = "pending"
PROVIDER_STATUS_RUNNING = "running"
PROVIDER_STATUS_OK = "ok"
PROVIDER_STATUS_EMPTY = "empty"
PROVIDER_STATUS_RATE_LIMITED = "rate_limited"
PROVIDER_STATUS_TIMEOUT = "timeout"
PROVIDER_STATUS_FAILED = "failed"
PROVIDER_STATUS_DISABLED = "disabled"
STALE_CITATION_RECOVERY_AFTER = timedelta(seconds=60)


def _source_identity_key(source: Source) -> tuple[str, str]:
    if source.doi:
        return ("doi", source.doi.strip().lower())
    if source.url:
        return ("url", source.url.strip().lower())
    normalized_title = " ".join((source.title or "").strip().lower().split())
    return ("title", f"{normalized_title}|{source.year or ''}")


def _run_session_scope(run: Run | None) -> tuple[str | None, str | None]:
    if run is None:
        return (None, None)
    session_id = (run.session_id or "").strip() or None
    context_key = (run.session_context_key or "").strip() or session_context_key(run.session_context)
    return (session_id, context_key)


def _provider_enabled(provider_name: str) -> bool:
    if provider_name == "semantic_scholar":
        return bool(settings.use_semantic_scholar)
    if provider_name == "brave":
        return bool(settings.brave_api_key)
    return True


def _provider_status_attrs(provider_name: str) -> tuple[str, str]:
    return (f"{provider_name}_status", f"{provider_name}_error_message")


def _set_query_provider_status(
    query_row: DiscoveryRunQuery,
    provider_name: str,
    status: str,
    error_message: str | None = None,
) -> None:
    status_attr, error_attr = _provider_status_attrs(provider_name)
    setattr(query_row, status_attr, status)
    setattr(query_row, error_attr, error_message)


def _reset_query_provider_statuses(query_row: DiscoveryRunQuery) -> None:
    for provider_name in ("openalex", "semantic_scholar", "brave"):
        _set_query_provider_status(
            query_row,
            provider_name,
            PROVIDER_STATUS_PENDING if _provider_enabled(provider_name) else PROVIDER_STATUS_DISABLED,
            None,
        )


def _provider_status_from_error(error_text: str) -> str:
    detail = (error_text or "").lower()
    if "429" in detail or "rate" in detail:
        return PROVIDER_STATUS_RATE_LIMITED
    if "timeout" in detail:
        return PROVIDER_STATUS_TIMEOUT
    return PROVIDER_STATUS_FAILED


def _evaluate_ai_with_watchdog(
    ai_filter: AIRelevanceFilter,
    *,
    run_id: str,
    query_id: str | None,
    title: str,
    kwargs: dict,
) -> object | None:
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result = ai_filter.evaluate(**kwargs)
            result_queue.put(("result", result))
        except Exception as exc:  # pragma: no cover - defensive propagation
            result_queue.put(("error", exc))

    worker = threading.Thread(
        target=_worker,
        name=f"km-ai-eval-{run_id[:8]}",
        daemon=True,
    )
    worker.start()
    configured_timeout = getattr(ai_filter, "timeout_seconds", None)
    try:
        configured_seconds = float(configured_timeout if configured_timeout is not None else settings.ai_timeout_seconds)
    except (TypeError, ValueError):
        configured_seconds = float(settings.ai_timeout_seconds)
    timeout_seconds = min(max(configured_seconds, AI_EVALUATION_WATCHDOG_FLOOR_SECONDS), AI_EVALUATION_WATCHDOG_MAX_SECONDS)
    try:
        kind, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        if hasattr(ai_filter, "mark_timeout"):
            ai_filter.mark_timeout()
        logger.warning(
            "ai_filter_watchdog_timeout run_id=%s query_id=%s timeout_seconds=%.2f title=%s",
            run_id,
            query_id or "-",
            timeout_seconds,
            title,
        )
        return None
    if kind == "error":
        raise payload  # type: ignore[misc]
    return payload


def _session_citation_parent_candidates(db: Session, *, target_run_id: str, session_id: str | None) -> list[Source]:
    if not (session_id or "").strip():
        return []
    rows = db.scalars(
        select(Source, Run)
        .join(Run, Run.id == Source.run_id)
        .where(Run.session_id == session_id, Source.accepted.is_(True))
        .order_by(Source.relevance_score.desc(), Source.updated_at.desc(), Source.id.asc())
    ).all()
    best_by_identity: dict[tuple[str, str], Source] = {}
    for source in rows:
        identity = _source_identity_key(source)
        current = best_by_identity.get(identity)
        if current is None:
            best_by_identity[identity] = source
            continue
        current_in_target = current.run_id == target_run_id
        candidate_in_target = source.run_id == target_run_id
        if candidate_in_target and not current_in_target:
            best_by_identity[identity] = source
            continue
        if candidate_in_target == current_in_target:
            current_rank = (
                float(current.relevance_score or 0.0),
                current.updated_at or current.created_at,
                current.id,
            )
            candidate_rank = (
                float(source.relevance_score or 0.0),
                source.updated_at or source.created_at,
                source.id,
            )
            if candidate_rank > current_rank:
                best_by_identity[identity] = source
    return list(best_by_identity.values())


def _citation_parent_expanded_in_context(db: Session, source: Source, *, run: Run | None) -> bool:
    session_id, context_key = _run_session_scope(run)
    if not session_id or not context_key:
        return bool(run is not None and db.get(CitationExpansionParent, (run.id, source.id)) is not None)
    target_identity = _source_identity_key(source)
    rows = db.execute(
        select(Source)
        .join(CitationExpansionParent, CitationExpansionParent.parent_source_id == Source.id)
        .where(
            CitationExpansionParent.session_id == session_id,
            CitationExpansionParent.session_context_key == context_key,
        )
    ).scalars().all()
    return any(_source_identity_key(existing) == target_identity for existing in rows)


def session_citation_parent_ids(db: Session, *, target_run_id: str, session_id: str | None) -> list[str]:
    run = db.get(Run, target_run_id)
    return [
        source.id
        for source in _session_citation_parent_candidates(db, target_run_id=target_run_id, session_id=session_id)
        if not _citation_parent_expanded_in_context(db, source, run=run)
    ]


def session_citation_parent_total(db: Session, *, target_run_id: str, session_id: str | None) -> int:
    return len(_session_citation_parent_candidates(db, target_run_id=target_run_id, session_id=session_id))


def create_run(
    db: Session,
    seed_queries: list[str],
    max_iterations: int,
    *,
    session_id: str | None = None,
    session_context: str | None = None,
    ai_filter_enabled: bool | None = None,
    provider_limits: dict[str, int] | None = None,
) -> Run:
    normalized_queries = _normalize_queries(seed_queries)
    if not normalized_queries:
        raise ValueError("seed_queries_required")
    normalized_session_context = normalize_session_context(session_context)
    normalized_session_id = (session_id or "").strip()
    if not normalized_session_id:
        normalized_session_id = f"legacy_session_{uuid.uuid4().hex[:12]}"
    use_ai_filter = settings.use_ai_filter if ai_filter_enabled is None else ai_filter_enabled
    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=use_ai_filter,
        api_key=settings.ai_api_key,
    )
    run = Run(
        id=f"run_{uuid.uuid4().hex[:12]}",
        status="queued",
        seed_queries=normalized_queries,
        session_id=normalized_session_id,
        session_context=normalized_session_context,
        session_context_key=session_context_key(normalized_session_context),
        max_iterations=max_iterations,
        current_iteration=0,
        accepted_total=0,
        expanded_candidates_total=0,
        citation_edges_total=0,
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
    )
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run.id)
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run.id)
    restore_expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.add(run)
        for position, query in enumerate(normalized_queries, start=1):
            db.add(
                DiscoveryRunQuery(
                    id=f"run_query_{uuid.uuid4().hex[:12]}",
                    run_id=run.id,
                    query_text=query,
                    query_metadata=_query_context_metadata(
                        session_id=normalized_session_id,
                        session_context=normalized_session_context,
                        provider_limits=provider_limits,
                    ),
                    position=position,
                    status="waiting",
                    discovered_count=0,
                    openalex_count=0,
                    brave_count=0,
                    semantic_scholar_count=0,
                    accepted_count=0,
                    rejected_count=0,
                    pending_count=0,
                    processing_count=0,
                )
            )
        db.commit()
    finally:
        db.expire_on_commit = restore_expire_on_commit
    return run


def create_bookmark_seeded_run(
    db: Session,
    *,
    session_id: str,
    session_context: str,
    bookmark: Bookmark,
    seed_source_ids: list[str],
) -> Run:
    normalized_session_id = (session_id or "").strip()
    normalized_session_context = normalize_session_context(session_context)
    if not normalized_session_id:
        raise ValueError("session_id_required")
    if not seed_source_ids:
        raise ValueError("bookmark_seed_sources_required")
    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=settings.use_ai_filter,
        api_key=settings.ai_api_key,
    )
    run = Run(
        id=f"run_{uuid.uuid4().hex[:12]}",
        status="queued",
        seed_queries=[],
        session_id=normalized_session_id,
        session_context=normalized_session_context,
        session_context_key=session_context_key(normalized_session_context),
        max_iterations=1,
        current_iteration=0,
        accepted_total=0,
        expanded_candidates_total=0,
        citation_edges_total=0,
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
    )
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run.id)
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run.id)
    restore_expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.add(run)
        for position, source_id in enumerate(seed_source_ids, start=1):
            db.add(
                DiscoveryCitationSeed(
                    run_id=run.id,
                    seed_source_id=source_id,
                    origin_bookmark_id=bookmark.id,
                    origin_session_id=bookmark.source_session_id,
                    seed_kind="bookmark",
                    position=position,
                )
            )
        db.commit()
    finally:
        db.expire_on_commit = restore_expire_on_commit
    return run


def enqueue_run(run_id: str) -> None:
    if not is_primary_instance():
        return
    run_lock = acquire_run_lock(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run_id)
    if run_lock is None:
        return
    worker = threading.Thread(target=_execute_run_with_lock, args=(run_id, run_lock), daemon=True)
    worker.start()


def enqueue_citation_iteration_run(run_id: str, *, source_run_id: str) -> None:
    if not is_primary_instance():
        return
    run_lock = acquire_run_lock(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
    if run_lock is None:
        return
    worker = threading.Thread(
        target=_execute_citation_run_with_lock,
        args=(run_id, source_run_id, run_lock),
        daemon=True,
    )
    worker.start()


def enqueue_bookmark_seed_run(run_id: str) -> None:
    if not is_primary_instance():
        return
    run_lock = acquire_run_lock(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
    if run_lock is None:
        return
    worker = threading.Thread(
        target=_execute_bookmark_seed_run_with_lock,
        args=(run_id, run_lock),
        daemon=True,
    )
    worker.start()


def resume_queued_discovery_runs() -> list[str]:
    if not is_primary_instance():
        return []
    with SessionLocal() as db:
        now = datetime.now(UTC)
        stale_running_ids: list[str] = []
        running_runs = db.scalars(
            select(Run)
            .where(Run.status == "running")
            .order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
        for run in running_runs:
            query_rows = db.scalars(
                select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id).order_by(DiscoveryRunQuery.position.asc())
            ).all()
            if not query_rows:
                continue
            if any((row.query_text or "").strip() == "citation expansion" for row in query_rows):
                continue
            nonterminal_statuses = {"waiting", "searching", "ranking_relevance"}
            terminal_statuses = {"completed"}
            if any((row.status or "").strip() not in (nonterminal_statuses | terminal_statuses) for row in query_rows):
                continue
            if not any((row.status or "").strip() in nonterminal_statuses for row in query_rows):
                continue
            run.status = "queued"
            run.error_message = None
            run.updated_at = now
            stale_running_ids.append(run.id)
            for row in query_rows:
                if (row.status or "").strip() == "completed":
                    continue
                row.status = "waiting"
                row.error_message = None
                row.started_at = None
                row.completed_at = None
                row.discovered_count = 0
                row.openalex_count = 0
                row.semantic_scholar_count = 0
                row.brave_count = 0
                row.accepted_count = 0
                row.rejected_count = 0
                row.pending_count = 0
                row.processing_count = 0
                row.updated_at = now
        if stale_running_ids:
            db.commit()
            logger.info(
                "stale_running_discovery_recovery reset_count=%s run_ids=%s",
                len(stale_running_ids),
                ",".join(stale_running_ids),
            )

        run_ids = db.scalars(
            select(Run.id)
            .where(Run.status == "queued")
            .order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
    for run_id in run_ids:
        enqueue_run(run_id)
    if run_ids:
        logger.info("discovery_queue_recovery resumed_count=%s run_ids=%s", len(run_ids), ",".join(run_ids))
    return run_ids


def mark_interrupted_runs_on_startup() -> list[str]:
    if not is_primary_instance():
        return []
    interrupted_run_ids: list[str] = []
    with SessionLocal() as db:
        now = datetime.now(UTC)
        active_runs = db.scalars(
            select(Run)
            .where(Run.status.in_(("queued", "running")))
            .order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
        for run in active_runs:
            query_rows = db.scalars(
                select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id).order_by(DiscoveryRunQuery.position.asc())
            ).all()
            if not query_rows:
                run.status = "failed"
                run.error_message = "Operation was not completed because the server restarted."
                run.updated_at = now
                interrupted_run_ids.append(run.id)
                continue
            has_resumable_citation = False
            changed = False
            for row in query_rows:
                row_status = (row.status or "").strip()
                if row_status in {"completed", "failed"}:
                    continue
                row.processing_count = 0
                row.updated_at = now
                if (row.query_text or "").strip() == "citation expansion":
                    row.status = "failed"
                    row.checkpoint_state = "resumable"
                    row.error_message = "Operation was not completed because the server restarted. Resume citation expansion manually."
                    has_resumable_citation = True
                else:
                    row.status = "failed"
                    row.error_message = "Operation was not completed because the server restarted. Start discovery again manually."
                changed = True
            if not changed:
                continue
            run.status = "failed"
            run.error_message = (
                "Operation was not completed because the server restarted. Resume citation expansion manually."
                if has_resumable_citation
                else "Operation was not completed because the server restarted. Start discovery again manually."
            )
            run.updated_at = now
            interrupted_run_ids.append(run.id)
        if interrupted_run_ids:
            db.commit()
            logger.info(
                "startup_interrupted_run_marking interrupted_count=%s run_ids=%s",
                len(interrupted_run_ids),
                ",".join(interrupted_run_ids),
            )
    return interrupted_run_ids


def resume_stale_citation_runs() -> list[str]:
    if not is_primary_instance():
        return []
    auto_resumed_run_ids: list[str] = []
    with SessionLocal() as db:
        now = datetime.now(UTC)
        running_runs = db.scalars(
            select(Run)
            .where(Run.status == "running")
            .order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
        for run in running_runs:
            query_row = db.scalars(
                select(DiscoveryRunQuery)
                .where(
                    DiscoveryRunQuery.run_id == run.id,
                    DiscoveryRunQuery.query_text == "citation expansion",
                    DiscoveryRunQuery.status.in_(("searching", "ranking_relevance")),
                    DiscoveryRunQuery.checkpoint_state == "running",
                )
                .order_by(DiscoveryRunQuery.position.desc())
                .limit(1)
            ).first()
            if query_row is None:
                continue
            updated_at = query_row.updated_at or run.updated_at or run.created_at
            if updated_at is None:
                continue
            updated_at_utc = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
            if (now - updated_at_utc) < STALE_CITATION_RECOVERY_AFTER:
                continue

            checkpoint = _load_checkpoint(run.id, query_row.id) or {}
            remaining_parent_ids = checkpoint.get("remaining_parent_ids")
            restored = _restore_citation_checkpoint_snapshot(run, query_row, checkpoint)
            if restored:
                clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run.id)
                clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run.id)
                logger.warning(
                    "stale_citation_recovery run_id=%s query_id=%s recovery_mode=auto_resume checkpoint_updated_at=%s restored_processed_parents=%s restored_remaining_parent_count=%s",
                    run.id,
                    query_row.id,
                    checkpoint.get("updated_at", "-"),
                    checkpoint.get("processed_parents", 0),
                    len(remaining_parent_ids) if isinstance(remaining_parent_ids, list) else 0,
                )
                db.commit()
                auto_resumed_run_ids.append(run.id)
                continue

            _legacy_mark_citation_resumable(run, query_row)
            clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run.id)
            clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run.id)
            logger.warning(
                "stale_citation_recovery_legacy_checkpoint run_id=%s query_id=%s checkpoint_updated_at=%s",
                run.id,
                query_row.id,
                checkpoint.get("updated_at", "-"),
            )
            db.commit()

    for run_id in auto_resumed_run_ids:
        with SessionLocal() as db:
            has_bookmark_seed = db.scalar(
                select(func.count())
                .select_from(DiscoveryCitationSeed)
                .where(DiscoveryCitationSeed.run_id == run_id)
            )
        if has_bookmark_seed:
            enqueue_bookmark_seed_run(run_id)
        else:
            enqueue_citation_iteration_run(run_id, source_run_id=run_id)
    if auto_resumed_run_ids:
        logger.info(
            "stale_citation_queue_recovery resumed_count=%s run_ids=%s",
            len(auto_resumed_run_ids),
            ",".join(auto_resumed_run_ids),
        )
    return auto_resumed_run_ids


def _execute_run_with_lock(run_id: str, run_lock: Path) -> None:
    try:
        execute_run_by_id(run_id)
    except Exception:
        logger.exception("discovery_run_worker_failed run_id=%s", run_id)
        raise
    finally:
        release_run_lock(run_lock)


def _execute_citation_run_with_lock(run_id: str, source_run_id: str, run_lock: Path) -> None:
    try:
        execute_citation_iteration_run_by_id(run_id, source_run_id=source_run_id)
    except Exception:
        logger.exception("citation_run_worker_failed run_id=%s source_run_id=%s", run_id, source_run_id)
        raise
    finally:
        release_run_lock(run_lock)


def _execute_bookmark_seed_run_with_lock(run_id: str, run_lock: Path) -> None:
    try:
        execute_bookmark_seed_run_by_id(run_id)
    except Exception:
        logger.exception("bookmark_seed_run_worker_failed run_id=%s", run_id)
        raise
    finally:
        release_run_lock(run_lock)


def execute_run_by_id(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        execute_run(db, run)


def execute_citation_iteration_run_by_id(run_id: str, *, source_run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        execute_citation_iteration_run(db, run, source_run_id=source_run_id)


def execute_bookmark_seed_run_by_id(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        execute_bookmark_seed_run(db, run)


@dataclass(slots=True)
class IngestStats:
    new_accepted_unique: int = 0
    accepted: int = 0
    rejected: int = 0
    pending: int = 0
    processing: int = 0


def _checkpoint_dir() -> Path:
    path = Path(settings.runtime_state_dir) / "citation_checkpoints"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _checkpoint_path(run_id: str, query_id: str) -> Path:
    return _checkpoint_dir() / f"{run_id}_{query_id}.json"


def _load_checkpoint(run_id: str, query_id: str) -> dict | None:
    path = _checkpoint_path(run_id, query_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_checkpoint(run_id: str, query_id: str, payload: dict) -> None:
    path = _checkpoint_path(run_id, query_id)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _clear_checkpoint(run_id: str, query_id: str) -> None:
    path = _checkpoint_path(run_id, query_id)
    if path.exists():
        path.unlink(missing_ok=True)


def _citation_query_checkpoint_state(query_row: DiscoveryRunQuery) -> dict[str, object]:
    return {
        "status": query_row.status,
        "checkpoint_state": query_row.checkpoint_state,
        "discovered_count": int(query_row.discovered_count or 0),
        "openalex_count": int(query_row.openalex_count or 0),
        "semantic_scholar_count": int(query_row.semantic_scholar_count or 0),
        "brave_count": int(query_row.brave_count or 0),
        "accepted_count": int(query_row.accepted_count or 0),
        "rejected_count": int(query_row.rejected_count or 0),
        "pending_count": int(query_row.pending_count or 0),
        "processing_count": 0,
        "scope_processed_parents": int(query_row.scope_processed_parents or 0),
        "scope_total_parents": int(query_row.scope_total_parents or 0),
        "openalex_status": getattr(query_row, "openalex_status", PROVIDER_STATUS_PENDING),
        "semantic_scholar_status": getattr(query_row, "semantic_scholar_status", PROVIDER_STATUS_PENDING),
        "brave_status": getattr(query_row, "brave_status", PROVIDER_STATUS_PENDING),
        "openalex_error_message": getattr(query_row, "openalex_error_message", None),
        "semantic_scholar_error_message": getattr(query_row, "semantic_scholar_error_message", None),
        "brave_error_message": getattr(query_row, "brave_error_message", None),
    }


def _citation_run_checkpoint_state(run: Run) -> dict[str, object]:
    return {
        "status": run.status,
        "error_message": run.error_message,
        "current_iteration": int(run.current_iteration or 0),
        "expanded_candidates_total": int(run.expanded_candidates_total or 0),
        "citation_edges_total": int(run.citation_edges_total or 0),
        "accepted_total": int(run.accepted_total or 0),
        "new_accept_rate": float(run.new_accept_rate or 0.0),
    }


def _save_citation_checkpoint(
    run: Run,
    query_row: DiscoveryRunQuery,
    *,
    processed_parents: int,
    remaining_parent_ids: list[str],
) -> None:
    payload = {
        "processed_parents": processed_parents,
        "remaining_parent_ids": remaining_parent_ids,
        "updated_at": datetime.now(UTC).isoformat(),
        "query_state": _citation_query_checkpoint_state(query_row),
        "run_state": _citation_run_checkpoint_state(run),
    }
    _save_checkpoint(run.id, query_row.id, payload)


def _restore_citation_checkpoint_snapshot(run: Run, query_row: DiscoveryRunQuery, checkpoint: dict[str, object]) -> bool:
    query_state = checkpoint.get("query_state")
    run_state = checkpoint.get("run_state")
    if not isinstance(query_state, dict) or not isinstance(run_state, dict):
        return False

    query_row.status = "searching"
    query_row.checkpoint_state = "resumable"
    query_row.error_message = None
    query_row.discovered_count = int(query_state.get("discovered_count", 0))
    query_row.openalex_count = int(query_state.get("openalex_count", 0))
    query_row.semantic_scholar_count = int(query_state.get("semantic_scholar_count", 0))
    query_row.brave_count = int(query_state.get("brave_count", 0))
    query_row.accepted_count = int(query_state.get("accepted_count", 0))
    query_row.rejected_count = int(query_state.get("rejected_count", 0))
    query_row.pending_count = int(query_state.get("pending_count", 0))
    query_row.processing_count = 0
    query_row.scope_processed_parents = int(query_state.get("scope_processed_parents", checkpoint.get("processed_parents", 0)))
    query_row.scope_total_parents = int(query_state.get("scope_total_parents", query_row.scope_total_parents or 0))
    for provider_name in ("openalex", "semantic_scholar", "brave"):
        status_attr, error_attr = _provider_status_attrs(provider_name)
        setattr(query_row, status_attr, query_state.get(status_attr, getattr(query_row, status_attr)))
        setattr(query_row, error_attr, query_state.get(error_attr))
    query_row.updated_at = datetime.now(UTC)

    run.status = "queued"
    run.error_message = None
    run.current_iteration = int(run_state.get("current_iteration", run.current_iteration or 0))
    run.expanded_candidates_total = int(run_state.get("expanded_candidates_total", run.expanded_candidates_total or 0))
    run.citation_edges_total = int(run_state.get("citation_edges_total", run.citation_edges_total or 0))
    run.accepted_total = int(run_state.get("accepted_total", run.accepted_total or 0))
    run.new_accept_rate = float(run_state.get("new_accept_rate", run.new_accept_rate or 0.0))
    run.updated_at = datetime.now(UTC)
    return True


def _legacy_mark_citation_resumable(run: Run, query_row: DiscoveryRunQuery) -> None:
    message = "recovery_requires_manual_resume_legacy_checkpoint"
    query_row.status = "failed"
    query_row.checkpoint_state = "resumable"
    query_row.error_message = message
    query_row.processing_count = 0
    query_row.updated_at = datetime.now(UTC)
    run.status = "failed"
    run.error_message = message
    run.updated_at = datetime.now(UTC)


def execute_run(db: Session, run: Run, connectors: list[Connector] | None = None) -> None:
    observability = RunObservability()
    run_id = run.id
    max_iterations = int(run.max_iterations)
    queries = _load_run_queries(db, run.id) or list(run.seed_queries)
    ai_warning = run.ai_filter_warning
    current_iteration = 0
    try:
        run.status = "running"
        ai_requested = bool(run.ai_filter_active)
        ai_effective_enabled = bool(ai_requested and settings.ai_api_key)
        if ai_requested and not ai_effective_enabled:
            ai_warning = "AI filter requested for run but effective AI config is missing at execution time; routing to needs_review."
            run.ai_filter_warning = ai_warning
        elif ai_requested and ai_effective_enabled and ai_warning:
            run.ai_filter_warning = None
            ai_warning = None
        db.commit()
        if ai_warning:
            observability.record_provider_call(
                run_id=run_id,
                iteration=0,
                provider="ai_filter",
                operation="runtime_warning",
                latency_ms=0.0,
                ok=False,
                error=ai_warning,
            )
        if ai_requested and not ai_effective_enabled:
            observability.inc("ai_provider_error")
            observability.record_provider_call(
                run_id=run_id,
                iteration=0,
                provider="ai_filter",
                operation="evaluate",
                latency_ms=0.0,
                ok=False,
                error="missing_config",
            )

        low_yield_streak = 0
        ai_filter = (
            AIRelevanceFilter(
                enabled=True,
                api_key=settings.ai_api_key,
                model=settings.ai_model,
                base_url=settings.ai_base_url,
                timeout_seconds=settings.ai_timeout_seconds,
            )
            if ai_effective_enabled
            else None
        )

        provider_limits = _provider_limits_for_run(db, run.id)
        active_connectors = connectors or build_connectors(provider_limits=provider_limits)
        for iteration in range(1, max_iterations + 1):
            _assert_discovery_not_stopped(run_id, phase="discovery")
            _ensure_run_queries(db, run_id, queries)
            query_batches = _collect_candidates(db, run_id, queries, iteration, active_connectors, observability=observability)
            new_accepted_unique = 0
            for run_query, query_candidates in query_batches:
                _assert_discovery_not_stopped(run_id, phase="discovery")
                if run_query.status == "failed":
                    continue
                run_query.status = "ranking_relevance"
                run_query.processing_count = len(query_candidates)
                run_query.updated_at = datetime.now(UTC)
                db.commit()
                stats = _ingest_candidates(
                    db,
                    run_id,
                    iteration,
                    query_candidates,
                    ai_filter=ai_filter,
                    ai_policy_no_ai=not ai_effective_enabled,
                    session_queries=list(run.seed_queries),
                    session_context=run.session_context,
                    query_id=run_query.id,
                    query_text=run_query.query_text,
                    observability=observability,
                )
                new_accepted_unique += stats.new_accepted_unique
                run_query.accepted_count = stats.accepted
                run_query.rejected_count = stats.rejected
                run_query.pending_count = stats.pending
                run_query.processing_count = 0
                run_query.status = "completed"
                run_query.completed_at = datetime.now(UTC)
                run_query.updated_at = datetime.now(UTC)
                db.commit()
            accepted_total = _count_accepted(db, run_id)

            _store_keywords_for_iteration(db, run_id, iteration)
            run = db.get(Run, run_id) or run
            run.current_iteration = iteration
            current_iteration = iteration
            run.accepted_total = accepted_total
            new_accept_rate = (new_accepted_unique / accepted_total) if accepted_total else 0.0
            run.new_accept_rate = new_accept_rate
            db.commit()

            if accepted_total > 0 and new_accept_rate < 0.05:
                low_yield_streak += 1
            else:
                low_yield_streak = 0

            if low_yield_streak >= 2:
                break

            queries = _next_iteration_queries(db, run_id, iteration)

        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status=run.status, current_iteration=current_iteration)
    except RunStopRequested:
        db.rollback()
        db_run = db.get(Run, run_id)
        if db_run is not None:
            db_run.status = "failed"
            db_run.error_message = "stopped_by_user"
            db_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=current_iteration)
    except Exception as exc:  # pragma: no cover - defensive failure path
        db.rollback()
        db_run = db.get(Run, run_id)
        if db_run is not None:
            db_run.status = "failed"
            db_run.error_message = str(exc)
            db_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run_id)
        observability.inc("api_errors")
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=current_iteration)
        raise


def execute_citation_iteration_run(db: Session, run: Run, *, source_run_id: str) -> None:
    observability = RunObservability()
    run_id = run.id
    source_run = db.get(Run, source_run_id)
    if source_run is None:
        run.status = "failed"
        run.error_message = f"source_run_not_found:{source_run_id}"
        run.updated_at = datetime.now(UTC)
        db.commit()
        observability.emit_run_summary(run_id=run_id, status=run.status, current_iteration=0)
        return

    ai_requested = bool(run.ai_filter_active)
    ai_effective_enabled = bool(ai_requested and settings.ai_api_key)
    ai_filter = (
        AIRelevanceFilter(
            enabled=True,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
        )
        if ai_effective_enabled
        else None
    )
    connectors = build_connectors(provider_limits=_provider_limits_for_run(db, run.id))
    connectors_by_name = {c.name: c for c in connectors}
    try:
        run.status = "running"
        run.error_message = None
        db.commit()
        # Resume an unfinished citation-expansion step when checkpoint exists.
        query_row = db.scalars(
            select(DiscoveryRunQuery)
            .where(
                DiscoveryRunQuery.run_id == run_id,
                DiscoveryRunQuery.query_text == "citation expansion",
                DiscoveryRunQuery.checkpoint_state.in_(("running", "resumable")),
            )
            .order_by(DiscoveryRunQuery.position.desc())
            .limit(1)
        ).first()
        if query_row is None:
            next_position = (
                db.scalar(
                    select(func.max(DiscoveryRunQuery.position)).where(DiscoveryRunQuery.run_id == run_id)
                )
                or 0
            ) + 1
            query_row = DiscoveryRunQuery(
                id=f"run_query_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                query_text="citation expansion",
                query_metadata=_query_context_metadata(
                    session_id=run.session_id,
                    session_context=run.session_context,
                    provider_limits=_provider_limits_for_run(db, run.id),
                ),
                position=next_position,
                status="searching",
                discovered_count=0,
                openalex_count=0,
                brave_count=0,
                semantic_scholar_count=0,
                accepted_count=0,
                rejected_count=0,
                pending_count=0,
                processing_count=0,
                scope_total_parents=0,
                scope_processed_parents=0,
                checkpoint_state="running",
            )
            db.add(query_row)
            query_row.started_at = datetime.now(UTC)
        else:
            query_row.status = "searching"
            query_row.checkpoint_state = "running"
            query_row.error_message = None
            if not isinstance(query_row.query_metadata, dict):
                query_row.query_metadata = {}
            if not query_row.query_metadata.get("session_context") and run.session_context:
                query_row.query_metadata = _query_context_metadata(
                    session_id=run.session_id,
                    session_context=run.session_context,
                    provider_limits=_provider_limits_for_run(db, run.id),
                )
        _reset_query_provider_statuses(query_row)
        query_row.updated_at = datetime.now(UTC)
        db.commit()

        checkpoint = _load_checkpoint(run_id, query_row.id) or {}
        parent_ids: list[str]
        processed_parents = int(checkpoint.get("processed_parents", 0))
        if isinstance(checkpoint.get("remaining_parent_ids"), list):
            parent_ids = [str(value) for value in checkpoint["remaining_parent_ids"]]
        else:
            session_id = source_run.session_id or run.session_id
            parent_ids = session_citation_parent_ids(db, target_run_id=run_id, session_id=session_id)
            processed_parents = 0
        query_row.scope_total_parents = len(parent_ids) + processed_parents
        query_row.scope_processed_parents = processed_parents
        query_row.updated_at = datetime.now(UTC)
        db.commit()

        for index, parent_id in enumerate(parent_ids):
            _assert_discovery_not_stopped(run_id, phase="discovery_citation")
            parent = db.get(Source, parent_id)
            if parent is None or not bool(parent.accepted):
                processed_parents += 1
                query_row.scope_processed_parents = processed_parents
                _save_citation_checkpoint(
                    run,
                    query_row,
                    processed_parents=processed_parents,
                    remaining_parent_ids=parent_ids[index + 1 :],
                )
                db.commit()
                continue
            query_row.status = "searching"
            query_row.checkpoint_state = "running"
            query_row.updated_at = datetime.now(UTC)
            db.commit()

            citation_candidates, citation_edges, provider_counts, provider_errors, citation_metrics = _expand_citations_for_parent_unbounded(
                run_id=run_id,
                parent=parent,
                connectors_by_name=connectors_by_name,
                observability=observability,
                iteration=max(int(run.current_iteration or 1), 1),
            )
            observability.record_citation_parent_counts(
                run_id=run_id,
                iteration=max(int(run.current_iteration or 1), 1),
                query_id=query_row.id,
                parent_source_id=parent.id,
                parent_title=parent.title,
                parent_doi=parent.doi,
                parent_provider=parent.source,
                provider_counts={name: int(count) for name, count in provider_counts.items()},
                provider_direction_counts={
                    name: int(count) for name, count in citation_metrics["provider_direction_counts"].items()
                },
                direction_overlap_counts={
                    name: int(count) for name, count in citation_metrics["direction_overlap_counts"].items()
                },
                direction_deduped_counts={
                    name: int(count) for name, count in citation_metrics["direction_deduped_counts"].items()
                },
                raw_total=sum(int(count) for count in provider_counts.values()),
                deduped_candidates=len(citation_candidates),
                edge_count=len(citation_edges),
            )
            query_row.discovered_count = int(query_row.discovered_count) + len(citation_candidates)
            query_row.openalex_count = int(query_row.openalex_count) + int(provider_counts.get("openalex", 0))
            query_row.brave_count = int(query_row.brave_count) + int(provider_counts.get("brave", 0))
            query_row.semantic_scholar_count = int(query_row.semantic_scholar_count) + int(
                provider_counts.get("semantic_scholar", 0)
            )
            for provider_name in ("openalex", "semantic_scholar", "brave"):
                if int(provider_counts.get(provider_name, 0)) > 0:
                    _set_query_provider_status(query_row, provider_name, PROVIDER_STATUS_OK, None)
                elif provider_name in provider_errors:
                    _set_query_provider_status(
                        query_row,
                        provider_name,
                        _provider_status_from_error(provider_errors[provider_name]),
                        provider_errors[provider_name],
                    )
                elif _provider_enabled(provider_name):
                    current_status = getattr(query_row, _provider_status_attrs(provider_name)[0])
                    if current_status in {PROVIDER_STATUS_PENDING, PROVIDER_STATUS_RUNNING}:
                        _set_query_provider_status(query_row, provider_name, PROVIDER_STATUS_EMPTY, None)
            query_row.status = "ranking_relevance"
            query_row.processing_count = len(citation_candidates)
            query_row.updated_at = datetime.now(UTC)
            db.commit()

            base_accepted = int(query_row.accepted_count or 0)
            base_rejected = int(query_row.rejected_count or 0)
            base_pending = int(query_row.pending_count or 0)
            stats = _ingest_candidates(
                db,
                run_id,
                max(int(run.current_iteration or 1), 1),
                citation_candidates,
                ai_filter=ai_filter,
                ai_policy_no_ai=not ai_effective_enabled,
                session_queries=list(run.seed_queries),
                session_context=run.session_context,
                query_id=query_row.id,
                query_text=query_row.query_text,
                observability=observability,
            )
            query_row.accepted_count = base_accepted + stats.accepted
            query_row.rejected_count = base_rejected + stats.rejected
            query_row.pending_count = base_pending + stats.pending
            query_row.processing_count = 0
            if citation_edges:
                persisted_edges = _persist_citation_edges(
                    db,
                    run_id,
                    max(int(run.current_iteration or 1), 1),
                    citation_edges,
                )
                run.citation_edges_total = int(run.citation_edges_total) + persisted_edges
            run.expanded_candidates_total = int(run.expanded_candidates_total) + len(citation_candidates)
            run.accepted_total = _count_accepted(db, run_id)
            run.new_accept_rate = (stats.new_accepted_unique / run.accepted_total) if run.accepted_total else 0.0
            db.merge(
                CitationExpansionParent(
                    run_id=run_id,
                    parent_source_id=parent_id,
                    session_id=run.session_id,
                    session_context_key=run.session_context_key,
                    query_id=query_row.id,
                )
            )
            processed_parents += 1
            query_row.scope_processed_parents = processed_parents
            query_row.updated_at = datetime.now(UTC)
            _save_citation_checkpoint(
                run,
                query_row,
                processed_parents=processed_parents,
                remaining_parent_ids=parent_ids[index + 1 :],
            )
            db.commit()

        _clear_checkpoint(run_id, query_row.id)
        query_row.status = "completed"
        query_row.checkpoint_state = "completed"
        query_row.completed_at = datetime.now(UTC)
        query_row.processing_count = 0
        query_row.updated_at = datetime.now(UTC)
        run.current_iteration = max(int(run.current_iteration or 0), 1)
        run.accepted_total = _count_accepted(db, run_id)
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status=run.status, current_iteration=run.current_iteration)
    except RunStopRequested:
        db.rollback()
        if "query_row" in locals() and query_row is not None:
            with suppress(Exception):
                refreshed_query = db.get(DiscoveryRunQuery, query_row.id)
                if refreshed_query is not None:
                    refreshed_query.checkpoint_state = "resumable"
                    refreshed_query.status = "failed"
                    refreshed_query.error_message = "stopped_by_user"
                    refreshed_query.updated_at = datetime.now(UTC)
                    db.commit()
        failed_run = db.get(Run, run_id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_message = "stopped_by_user"
            failed_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=0)
    except Exception as exc:  # pragma: no cover - defensive failure path
        db.rollback()
        if "query_row" in locals() and query_row is not None:
            with suppress(Exception):
                refreshed_query = db.get(DiscoveryRunQuery, query_row.id)
                if refreshed_query is not None:
                    refreshed_query.checkpoint_state = "resumable"
                    refreshed_query.status = "failed"
                    refreshed_query.error_message = str(exc)
                    refreshed_query.updated_at = datetime.now(UTC)
                    db.commit()
        failed_run = db.get(Run, run_id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_message = str(exc)
            failed_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.inc("api_errors")
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=0)
        raise


def _bookmark_seed_parent_ids(db: Session, run_id: str, *, query_id: str | None = None) -> list[str]:
    run = db.get(Run, run_id)
    rows = db.scalars(
        select(DiscoveryCitationSeed)
        .where(DiscoveryCitationSeed.run_id == run_id)
        .order_by(DiscoveryCitationSeed.position.asc())
    ).all()
    parent_ids: list[str] = []
    for row in rows:
        source = db.get(Source, row.seed_source_id)
        if source is None:
            continue
        if _citation_parent_expanded_in_context(db, source, run=run):
            continue
        parent_ids.append(row.seed_source_id)
    return parent_ids


def execute_bookmark_seed_run(db: Session, run: Run) -> None:
    observability = RunObservability()
    run_id = run.id
    ai_requested = bool(run.ai_filter_active)
    ai_effective_enabled = bool(ai_requested and settings.ai_api_key)
    ai_filter = (
        AIRelevanceFilter(
            enabled=True,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
        )
        if ai_effective_enabled
        else None
    )
    connectors = build_connectors(provider_limits=_provider_limits_for_run(db, run.id))
    connectors_by_name = {c.name: c for c in connectors}
    try:
        run.status = "running"
        run.error_message = None
        db.commit()
        query_row = db.scalars(
            select(DiscoveryRunQuery)
            .where(
                DiscoveryRunQuery.run_id == run_id,
                DiscoveryRunQuery.query_text == "citation expansion",
                DiscoveryRunQuery.checkpoint_state.in_(("running", "resumable")),
            )
            .order_by(DiscoveryRunQuery.position.desc())
            .limit(1)
        ).first()
        if query_row is None:
            query_row = DiscoveryRunQuery(
                id=f"run_query_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                query_text="citation expansion",
                query_metadata={
                    **_query_context_metadata(
                        session_id=run.session_id,
                        session_context=run.session_context,
                        provider_limits=_provider_limits_for_run(db, run.id),
                    ),
                    "seed_kind": "bookmark",
                    "seeded_run": True,
                },
                position=1,
                status="searching",
                discovered_count=0,
                openalex_count=0,
                brave_count=0,
                semantic_scholar_count=0,
                accepted_count=0,
                rejected_count=0,
                pending_count=0,
                processing_count=0,
                scope_total_parents=0,
                scope_processed_parents=0,
                checkpoint_state="running",
            )
            db.add(query_row)
            query_row.started_at = datetime.now(UTC)
        else:
            query_row.status = "searching"
            query_row.checkpoint_state = "running"
            query_row.error_message = None
            if not isinstance(query_row.query_metadata, dict):
                query_row.query_metadata = {}
            query_row.query_metadata = {
                **query_row.query_metadata,
                **_query_context_metadata(
                    session_id=run.session_id,
                    session_context=run.session_context,
                    provider_limits=_provider_limits_for_run(db, run.id),
                ),
                "seed_kind": "bookmark",
                "seeded_run": True,
            }
        _reset_query_provider_statuses(query_row)
        query_row.updated_at = datetime.now(UTC)
        db.commit()

        checkpoint = _load_checkpoint(run_id, query_row.id) or {}
        processed_parents = int(checkpoint.get("processed_parents", 0))
        if isinstance(checkpoint.get("remaining_parent_ids"), list):
            parent_ids = [str(value) for value in checkpoint["remaining_parent_ids"]]
        else:
            parent_ids = _bookmark_seed_parent_ids(db, run_id, query_id=query_row.id)
            processed_parents = 0
        query_row.scope_total_parents = len(parent_ids) + processed_parents
        query_row.scope_processed_parents = processed_parents
        query_row.updated_at = datetime.now(UTC)
        db.commit()

        for index, parent_id in enumerate(parent_ids):
            _assert_discovery_not_stopped(run_id, phase="discovery_citation")
            parent = db.get(Source, parent_id)
            if parent is None:
                processed_parents += 1
                query_row.scope_processed_parents = processed_parents
                _save_citation_checkpoint(
                    run,
                    query_row,
                    processed_parents=processed_parents,
                    remaining_parent_ids=parent_ids[index + 1 :],
                )
                db.commit()
                continue
            query_row.status = "searching"
            query_row.checkpoint_state = "running"
            query_row.updated_at = datetime.now(UTC)
            db.commit()

            citation_candidates, citation_edges, provider_counts, provider_errors, citation_metrics = _expand_citations_for_parent_unbounded(
                run_id=run_id,
                parent=parent,
                connectors_by_name=connectors_by_name,
                observability=observability,
                iteration=1,
            )
            observability.record_citation_parent_counts(
                run_id=run_id,
                iteration=1,
                query_id=query_row.id,
                parent_source_id=parent.id,
                parent_title=parent.title,
                parent_doi=parent.doi,
                parent_provider=parent.source,
                provider_counts={name: int(count) for name, count in provider_counts.items()},
                provider_direction_counts={
                    name: int(count) for name, count in citation_metrics["provider_direction_counts"].items()
                },
                direction_overlap_counts={
                    name: int(count) for name, count in citation_metrics["direction_overlap_counts"].items()
                },
                direction_deduped_counts={
                    name: int(count) for name, count in citation_metrics["direction_deduped_counts"].items()
                },
                raw_total=sum(int(count) for count in provider_counts.values()),
                deduped_candidates=len(citation_candidates),
                edge_count=len(citation_edges),
            )
            query_row.discovered_count = int(query_row.discovered_count) + len(citation_candidates)
            query_row.openalex_count = int(query_row.openalex_count) + int(provider_counts.get("openalex", 0))
            query_row.brave_count = int(query_row.brave_count) + int(provider_counts.get("brave", 0))
            query_row.semantic_scholar_count = int(query_row.semantic_scholar_count) + int(
                provider_counts.get("semantic_scholar", 0)
            )
            for provider_name in ("openalex", "semantic_scholar", "brave"):
                if int(provider_counts.get(provider_name, 0)) > 0:
                    _set_query_provider_status(query_row, provider_name, PROVIDER_STATUS_OK, None)
                elif provider_name in provider_errors:
                    _set_query_provider_status(
                        query_row,
                        provider_name,
                        _provider_status_from_error(provider_errors[provider_name]),
                        provider_errors[provider_name],
                    )
                elif _provider_enabled(provider_name):
                    current_status = getattr(query_row, _provider_status_attrs(provider_name)[0])
                    if current_status in {PROVIDER_STATUS_PENDING, PROVIDER_STATUS_RUNNING}:
                        _set_query_provider_status(query_row, provider_name, PROVIDER_STATUS_EMPTY, None)
            query_row.status = "ranking_relevance"
            query_row.processing_count = len(citation_candidates)
            query_row.updated_at = datetime.now(UTC)
            db.commit()

            base_accepted = int(query_row.accepted_count or 0)
            base_rejected = int(query_row.rejected_count or 0)
            base_pending = int(query_row.pending_count or 0)
            stats = _ingest_candidates(
                db,
                run_id,
                1,
                citation_candidates,
                ai_filter=ai_filter,
                ai_policy_no_ai=not ai_effective_enabled,
                session_queries=[],
                session_context=run.session_context,
                query_id=query_row.id,
                query_text=query_row.query_text,
                observability=observability,
            )
            query_row.accepted_count = base_accepted + stats.accepted
            query_row.rejected_count = base_rejected + stats.rejected
            query_row.pending_count = base_pending + stats.pending
            query_row.processing_count = 0
            if citation_edges:
                persisted_edges = _persist_citation_edges(db, run_id, 1, citation_edges)
                run.citation_edges_total = int(run.citation_edges_total) + persisted_edges
            run.expanded_candidates_total = int(run.expanded_candidates_total) + len(citation_candidates)
            run.accepted_total = _count_accepted(db, run_id)
            run.new_accept_rate = (stats.new_accepted_unique / run.accepted_total) if run.accepted_total else 0.0
            db.merge(
                CitationExpansionParent(
                    run_id=run_id,
                    parent_source_id=parent_id,
                    session_id=run.session_id,
                    session_context_key=run.session_context_key,
                    query_id=query_row.id,
                )
            )
            processed_parents += 1
            query_row.scope_processed_parents = processed_parents
            query_row.updated_at = datetime.now(UTC)
            _save_citation_checkpoint(
                run,
                query_row,
                processed_parents=processed_parents,
                remaining_parent_ids=parent_ids[index + 1 :],
            )
            db.commit()

        _clear_checkpoint(run_id, query_row.id)
        query_row.status = "completed"
        query_row.checkpoint_state = "completed"
        query_row.completed_at = datetime.now(UTC)
        query_row.processing_count = 0
        query_row.updated_at = datetime.now(UTC)
        run.current_iteration = 1
        run.accepted_total = _count_accepted(db, run_id)
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status=run.status, current_iteration=run.current_iteration)
    except RunStopRequested:
        db.rollback()
        if "query_row" in locals() and query_row is not None:
            with suppress(Exception):
                refreshed_query = db.get(DiscoveryRunQuery, query_row.id)
                if refreshed_query is not None:
                    refreshed_query.checkpoint_state = "resumable"
                    refreshed_query.status = "failed"
                    refreshed_query.error_message = "stopped_by_user"
                    refreshed_query.updated_at = datetime.now(UTC)
                    db.commit()
        failed_run = db.get(Run, run_id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_message = "stopped_by_user"
            failed_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=0)
    except Exception as exc:  # pragma: no cover - defensive failure path
        db.rollback()
        if "query_row" in locals() and query_row is not None:
            with suppress(Exception):
                refreshed_query = db.get(DiscoveryRunQuery, query_row.id)
                if refreshed_query is not None:
                    refreshed_query.checkpoint_state = "resumable"
                    refreshed_query.status = "failed"
                    refreshed_query.error_message = str(exc)
                    refreshed_query.updated_at = datetime.now(UTC)
                    db.commit()
        failed_run = db.get(Run, run_id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_message = str(exc)
            failed_run.updated_at = datetime.now(UTC)
            db.commit()
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        observability.inc("api_errors")
        observability.emit_run_summary(run_id=run_id, status="failed", current_iteration=0)
        raise


def _assert_discovery_not_stopped(run_id: str, *, phase: str) -> None:
    if is_run_stop_requested(base_dir=settings.runtime_state_dir, phase=phase, run_id=run_id):
        raise RunStopRequested("stopped_by_user")


def review_source(db: Session, source: Source, decision: str) -> Source:
    normalized = decision.strip().lower()
    if normalized not in {"accept", "reject", "later"}:
        raise ValueError("decision must be accept, reject, or later")

    if normalized == "accept":
        source.accepted = True
        source.review_status = "human_accept"
        source.final_decision = "human_accept"
    elif normalized == "reject":
        source.accepted = False
        source.review_status = "human_reject"
        source.final_decision = "human_reject"
    else:
        source.accepted = False
        source.review_status = "human_later"
        source.final_decision = "human_later"
    source.decision_source = "human_review"
    source.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(source)
    return source


def export_sources_raw(db: Session, run_id: str) -> Path:
    sources = db.scalars(
        select(Source)
        .where(Source.run_id == run_id, Source.accepted.is_(True))
        .order_by(Source.accepted.desc(), Source.relevance_score.desc(), Source.year.desc().nullslast(), Source.id.asc())
    ).all()

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "year": s.year,
                "url": s.url,
                "doi": s.doi,
                "abstract": s.abstract,
                "journal": s.journal,
                "authors": list(s.authors or []),
                "citation_count": s.citation_count,
                "type": s.type,
                "source": s.source,
                "iteration": s.iteration,
                "discovery_method": s.discovery_method,
                "relevance_score": float(s.relevance_score),
                "accepted": s.accepted,
                "review_status": s.review_status,
                "final_decision": s.final_decision,
                "decision_source": s.decision_source,
                "heuristic_recommendation": s.heuristic_recommendation,
                "heuristic_score": float(s.heuristic_score),
                "parent_source": s.parent_source_id,
                "provenance_history": s.provenance_history,
            }
            for s in sources
        ],
        "provenance": {
            "seed_queries": list(_load_run_seed_queries(db, run_id)),
            "apis_used": ["openalex", "semantic_scholar", "brave"],
        },
    }

    out_dir = Path(settings.artifacts_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sources_raw.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def _load_run_seed_queries(db: Session, run_id: str) -> list[str]:
    run = db.get(Run, run_id)
    if run is None:
        return []
    return list(run.seed_queries)


def _normalize_queries(queries: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in queries:
        query = str(raw or "").strip()
        if not query:
            continue
        lowered = query.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(query)
    return normalized


def _load_run_queries(db: Session, run_id: str) -> list[str]:
    rows = db.scalars(
        select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run_id).order_by(DiscoveryRunQuery.position.asc())
    ).all()
    return [row.query_text for row in rows]


def _ensure_run_queries(db: Session, run_id: str, queries: list[str]) -> None:
    existing = db.scalars(
        select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run_id).order_by(DiscoveryRunQuery.position.asc())
    ).all()
    if existing:
        return
    run = db.get(Run, run_id)
    session_context = run.session_context if run is not None else None
    session_id = run.session_id if run is not None else None
    for position, query in enumerate(_normalize_queries(queries), start=1):
        db.add(
            DiscoveryRunQuery(
                id=f"run_query_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                query_text=query,
                query_metadata=_query_context_metadata(
                    session_id=session_id,
                    session_context=session_context,
                    provider_limits=_provider_limits_for_run(db, run_id),
                ),
                position=position,
                status="waiting",
                discovered_count=0,
                openalex_count=0,
                brave_count=0,
                semantic_scholar_count=0,
                accepted_count=0,
                rejected_count=0,
                pending_count=0,
                processing_count=0,
            )
        )
    db.commit()


def _collect_candidates(
    db: Session,
    run_id: str,
    queries: list[str],
    iteration: int,
    connectors: list[Connector],
    *,
    observability: RunObservability,
) -> list[tuple[DiscoveryRunQuery, list[dict]]]:
    query_batches: list[tuple[DiscoveryRunQuery, list[dict]]] = []
    run_queries = db.scalars(
        select(DiscoveryRunQuery)
        .where(DiscoveryRunQuery.run_id == run_id)
        .order_by(DiscoveryRunQuery.position.asc())
        .limit(10)
    ).all()
    run = db.get(Run, run_id)
    session_context = run.session_context if run is not None else None
    session_id = run.session_id if run is not None else None
    if not run_queries:
        run_queries = []
        for position, query in enumerate(_normalize_queries(queries)[:10], start=1):
            run_queries.append(
                DiscoveryRunQuery(
                    id=f"run_query_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    query_text=query,
                    query_metadata=_query_context_metadata(
                        session_id=session_id,
                        session_context=session_context,
                    ),
                    position=position,
                    status="waiting",
                    discovered_count=0,
                    openalex_count=0,
                    brave_count=0,
                    semantic_scholar_count=0,
                    accepted_count=0,
                    rejected_count=0,
                    pending_count=0,
                    processing_count=0,
                )
            )
            db.add(run_queries[-1])
        db.commit()

    for run_query in run_queries:
        if run_query.status in {"completed", "failed"}:
            continue
        if not isinstance(run_query.query_metadata, dict):
            run_query.query_metadata = {}
        if not run_query.query_metadata.get("session_context") and session_context:
            run_query.query_metadata = _query_context_metadata(
                session_id=session_id,
                session_context=session_context,
                provider_limits=_provider_limits_for_run(db, run_id),
            )
        query = run_query.query_text
        run_query.status = "searching"
        run_query.error_message = None
        run_query.openalex_count = 0
        run_query.brave_count = 0
        run_query.semantic_scholar_count = 0
        _reset_query_provider_statuses(run_query)
        run_query.accepted_count = 0
        run_query.rejected_count = 0
        run_query.pending_count = 0
        run_query.processing_count = 0
        run_query.started_at = datetime.now(UTC)
        run_query.updated_at = datetime.now(UTC)
        db.commit()
        query_rows: list[dict] = []
        query_errors: list[str] = []
        successful_connector_calls = 0
        provider_counts: Counter[str] = Counter()
        for connector in connectors:
            _set_query_provider_status(run_query, connector.name, PROVIDER_STATUS_RUNNING, None)
            run_query.updated_at = datetime.now(UTC)
            db.commit()
            started = time.perf_counter()
            try:
                rows = retry_call(
                    lambda: connector.search(query, run_id=run_id, iteration=iteration),
                    attempts=3,
                    delays=(1.0, 2.0, 4.0),
                    should_retry=lambda exc: isinstance(exc, RetryableProviderError),
                )
                observability.record_provider_call(
                    run_id=run_id,
                    iteration=iteration,
                    provider=connector.name,
                    operation="search",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    ok=True,
                )
            except Exception as exc:
                observability.inc("api_errors")
                observability.record_provider_call(
                    run_id=run_id,
                    iteration=iteration,
                    provider=connector.name,
                    operation="search",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    ok=False,
                    error=str(exc),
                )
                query_errors.append(f"{connector.name}:{exc}")
                _set_query_provider_status(
                    run_query,
                    connector.name,
                    _provider_status_from_error(str(exc)),
                    str(exc),
                )
                run_query.updated_at = datetime.now(UTC)
                db.commit()
                continue
            successful_connector_calls += 1
            observability.inc("fetched", len(rows))
            provider_counts[connector.name] += len(rows)
            _set_query_provider_status(
                run_query,
                connector.name,
                PROVIDER_STATUS_OK if rows else PROVIDER_STATUS_EMPTY,
                None,
            )
            run_query.updated_at = datetime.now(UTC)
            db.commit()
            query_rows.extend(rows)
        run_query.status = "ranking_relevance" if successful_connector_calls > 0 else "failed"
        run_query.discovered_count = len(query_rows)
        run_query.openalex_count = int(provider_counts.get("openalex", 0))
        run_query.brave_count = int(provider_counts.get("brave", 0))
        run_query.semantic_scholar_count = int(provider_counts.get("semantic_scholar", 0))
        run_query.processing_count = len(query_rows) if successful_connector_calls > 0 else 0
        run_query.error_message = "; ".join(query_errors[:3]) if query_errors and successful_connector_calls == 0 else None
        run_query.completed_at = datetime.now(UTC) if successful_connector_calls == 0 else None
        run_query.updated_at = datetime.now(UTC)
        db.commit()
        query_batches.append((run_query, query_rows))
    return query_batches


def _prefer_citation_candidate(current: dict, candidate: dict) -> dict:
    def score(row: dict) -> tuple[int, int, int, int]:
        return (
            int(bool(row.get("abstract"))),
            int(bool(row.get("doi"))),
            int(isinstance(row.get("citation_count"), int)),
            len(str(row.get("title") or "")),
        )

    return candidate if score(candidate) > score(current) else current


def _citation_connector_order(parent: Source, connectors_by_name: dict[str, Connector]) -> list[Connector]:
    ordered_names = [parent.source, "openalex", "semantic_scholar", "brave"]
    seen: set[str] = set()
    ordered: list[Connector] = []
    for name in ordered_names:
        if not name or name in seen:
            continue
        connector = connectors_by_name.get(name)
        if connector is None:
            continue
        seen.add(name)
        ordered.append(connector)
    for name, connector in connectors_by_name.items():
        if name in seen:
            continue
        seen.add(name)
        ordered.append(connector)
    return ordered


def _expand_citations_for_parent_unbounded(
    *,
    run_id: str,
    parent: Source,
    connectors_by_name: dict[str, Connector],
    observability: RunObservability,
    iteration: int,
) -> tuple[list[dict], list[tuple[str, str, str]], Counter[str], dict[str, str], dict[str, dict[str, int]]]:
    deduped_backward_candidates: dict[str, dict] = {}
    deduped_forward_candidates: dict[str, dict] = {}
    edge_triples: set[tuple[str, str, str]] = set()
    provider_counts: Counter[str] = Counter()
    provider_direction_counts: Counter[str] = Counter()
    provider_errors: dict[str, str] = {}
    provider_direction_target_ids: dict[str, set[str]] = {
        "openalex_backward": set(),
        "openalex_forward": set(),
        "semantic_scholar_backward": set(),
        "semantic_scholar_forward": set(),
        "brave_backward": set(),
        "brave_forward": set(),
    }

    for connector in _citation_connector_order(parent, connectors_by_name):
        started = time.perf_counter()
        try:
            backward, forward = retry_call(
                lambda: connector.expand_citations(parent, per_direction_limit=0, iteration=iteration),
                attempts=3,
                delays=(1.0, 2.0, 4.0),
                should_retry=lambda exc: isinstance(exc, RetryableProviderError),
            )
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=True,
            )
        except Exception as exc:
            observability.inc("api_errors")
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=False,
                error=str(exc),
            )
            provider_errors[connector.name] = str(exc)
            continue

        combined = list(backward) + list(forward)
        provider_counts[connector.name] += len(combined)
        provider_direction_counts[f"{connector.name}_backward"] += len(backward)
        provider_direction_counts[f"{connector.name}_forward"] += len(forward)
        observability.inc("fetched", len(combined))
        for c in backward:
            target_id = _candidate_target_id(c)
            provider_direction_target_ids[f"{connector.name}_backward"].add(target_id)
            existing = deduped_backward_candidates.get(target_id)
            deduped_backward_candidates[target_id] = c if existing is None else _prefer_citation_candidate(existing, c)
            edge_triples.add((parent.id, target_id, "cites"))
        for c in forward:
            target_id = _candidate_target_id(c)
            provider_direction_target_ids[f"{connector.name}_forward"].add(target_id)
            existing = deduped_forward_candidates.get(target_id)
            deduped_forward_candidates[target_id] = c if existing is None else _prefer_citation_candidate(existing, c)
            edge_triples.add((parent.id, target_id, "cited_by"))

    metrics = {
        "provider_direction_counts": dict(provider_direction_counts),
        "direction_overlap_counts": {
            "backward_overlap": len(
                provider_direction_target_ids["openalex_backward"] & provider_direction_target_ids["semantic_scholar_backward"]
            ),
            "forward_overlap": len(
                provider_direction_target_ids["openalex_forward"] & provider_direction_target_ids["semantic_scholar_forward"]
            ),
        },
        "direction_deduped_counts": {
            "backward_deduped": len(deduped_backward_candidates),
            "forward_deduped": len(deduped_forward_candidates),
        },
    }
    return (
        list(deduped_backward_candidates.values()) + list(deduped_forward_candidates.values()),
        list(edge_triples),
        provider_counts,
        provider_errors,
        metrics,
    )


def _expand_citations_for_iteration(
    db: Session,
    run_id: str,
    iteration: int,
    connectors_by_name: dict[str, Connector],
    *,
    per_direction_limit: int,
    parent_cap: int,
    observability: RunObservability,
) -> tuple[list[dict], list[tuple[str, str, str]]]:
    parents = db.scalars(
        select(Source)
        .where(Source.run_id == run_id, Source.iteration == iteration, Source.accepted.is_(True))
        .order_by(Source.relevance_score.desc(), Source.id.asc())
        .limit(max(0, parent_cap))
    ).all()

    expanded_candidates: list[dict] = []
    edge_triples: list[tuple[str, str, str]] = []
    for parent in parents:
        connector = connectors_by_name.get(parent.source)
        if connector is None:
            continue
        internal_limit = max(per_direction_limit * 3, per_direction_limit)
        started = time.perf_counter()
        try:
            backward, forward = retry_call(
                lambda: connector.expand_citations(parent, per_direction_limit=internal_limit, iteration=iteration),
                attempts=3,
                delays=(1.0, 2.0, 4.0),
                should_retry=lambda exc: isinstance(exc, RetryableProviderError),
            )
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=True,
            )
        except Exception as exc:
            observability.inc("api_errors")
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=False,
                error=str(exc),
            )
            continue

        ranked_backward = _rank_citation_candidates(parent, backward)[: max(0, per_direction_limit)]
        ranked_forward = _rank_citation_candidates(parent, forward)[: max(0, per_direction_limit)]
        observability.inc("fetched", len(ranked_backward) + len(ranked_forward))
        for c in ranked_backward:
            expanded_candidates.append(c)
            edge_triples.append((parent.id, _candidate_target_id(c), "cites"))
        for c in ranked_forward:
            expanded_candidates.append(c)
            edge_triples.append((parent.id, _candidate_target_id(c), "cited_by"))
    return expanded_candidates, edge_triples


def _candidate_target_id(candidate: dict) -> str:
    return canonical_id(
        doi=candidate.get("doi"),
        url=candidate.get("url"),
        title=candidate["title"],
        year=candidate.get("year"),
        openalex_id=(candidate.get("openalex_id") or candidate.get("source_native_id"))
        if candidate.get("source") == "openalex"
        else None,
        semantic_scholar_id=(candidate.get("semantic_scholar_id") or candidate.get("source_native_id"))
        if candidate.get("source") == "semantic_scholar"
        else None,
        patent_office=candidate.get("patent_office"),
        patent_number=candidate.get("patent_number"),
    )


def _rank_citation_candidates(parent: Source, candidates: list[dict]) -> list[dict]:
    parent_tokens = _citation_tokens(parent.title, parent.abstract)

    def sort_key(candidate: dict) -> tuple:
        title = str(candidate.get("title") or "")
        abstract = candidate.get("abstract")
        year = candidate.get("year") if isinstance(candidate.get("year"), int) else 0
        overlap = len(parent_tokens & _citation_tokens(title, abstract))
        has_abstract = int(bool(abstract))
        has_doi = int(bool(candidate.get("doi")))
        # Deterministic tie-break with canonical id and lowercased title.
        tie_id = _candidate_target_id(candidate)
        return (-has_abstract, -has_doi, -year, -overlap, title.lower(), tie_id)

    return sorted(candidates, key=sort_key)


def _citation_tokens(title: str | None, abstract: str | None) -> set[str]:
    text = f"{title or ''} {abstract or ''}".lower()
    tokens = {"".join(ch for ch in token if ch.isalnum()) for token in text.split()}
    return {t for t in tokens if len(t) >= 3}


def _persist_citation_edges(db: Session, run_id: str, iteration: int, edges: list[tuple[str, str, str]]) -> int:
    unique_edges = set(edges)
    for source_id, target_id, relationship_type in unique_edges:
        db.merge(
            CitationEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                run_id=run_id,
                iteration=iteration,
            )
        )
    db.commit()
    return len(unique_edges)


def _ingest_candidates(
    db: Session,
    run_id: str,
    iteration: int,
    candidates: Iterable[dict],
    *,
    ai_filter: AIRelevanceFilter | None = None,
    ai_policy_no_ai: bool = False,
    session_queries: list[str] | None = None,
    session_context: str | None = None,
    query_id: str | None = None,
    query_text: str | None = None,
    observability: RunObservability | None = None,
) -> IngestStats:
    stats = IngestStats()
    candidate_list = list(candidates)
    total_candidates = len(candidate_list)
    pending_source_ids: set[str] = set()
    logged_context = False
    query_row = db.get(DiscoveryRunQuery, query_id) if query_id else None
    query_step_number = query_row.position if query_row is not None else None
    base_accepted_count = int(query_row.accepted_count) if query_row is not None else 0
    base_rejected_count = int(query_row.rejected_count) if query_row is not None else 0
    base_pending_count = int(query_row.pending_count) if query_row is not None else 0
    next_query_source_number = (
        (
            db.scalar(
                select(func.max(Source.query_source_number)).where(
                    Source.run_id == run_id,
                    Source.query_id == query_id,
                )
            )
            or 0
        )
        if query_id
        else 0
    )

    def _inc(bucket: str, value: int = 1) -> None:
        setattr(stats, bucket, getattr(stats, bucket) + value)

    def _track_review_status(review_status: str) -> None:
        if review_status in {"auto_accept", "human_accept"}:
            _inc("accepted")
        elif review_status in {"auto_reject", "human_reject"}:
            _inc("rejected")
        elif review_status in {"processing"}:
            _inc("processing")
        else:
            _inc("pending")

    def _normalize_candidate_year(raw_year: object) -> int | None:
        if raw_year is None:
            return None
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            return None
        # Keep ingestion compatible with the current DB check constraint.
        if year < 1900 or year > 2100:
            return None
        return year

    def _commit_query_progress(processed: int) -> None:
        if not query_id:
            return
        query_row = db.get(DiscoveryRunQuery, query_id)
        if query_row is None:
            return
        query_row.accepted_count = base_accepted_count + stats.accepted
        query_row.rejected_count = base_rejected_count + stats.rejected
        query_row.pending_count = base_pending_count + stats.pending
        query_row.processing_count = max(total_candidates - processed, 0)
        query_row.updated_at = datetime.now(UTC)
        db.commit()

    for index, c in enumerate(candidate_list, start=1):
        normalized_year = _normalize_candidate_year(c.get("year"))
        canonical_sid = canonical_id(
            doi=c.get("doi"),
            url=c.get("url"),
            title=c["title"],
            year=normalized_year,
            openalex_id=(c.get("openalex_id") or c.get("source_native_id")) if c.get("source") == "openalex" else None,
            semantic_scholar_id=(c.get("semantic_scholar_id") or c.get("source_native_id"))
            if c.get("source") == "semantic_scholar"
            else None,
            patent_office=c.get("patent_office"),
            patent_number=c.get("patent_number"),
        )
        sid = _run_scoped_source_id(db, run_id, canonical_sid)

        if sid in pending_source_ids:
            if observability is not None:
                observability.inc("dedup")
            if query_id and (index % 5 == 0 or index == total_candidates):
                _commit_query_progress(index)
            continue

        existing = _find_existing_source(db, run_id, c, canonical_sid)
        if existing is not None:
            _merge_source(existing, c, iteration=iteration)
            db.add(existing)
            if observability is not None:
                observability.inc("dedup")
            # Only count rows already attached to this query. Duplicates that were
            # first discovered by a different query should not inflate this query's
            # accepted/rejected/pending counters.
            if not query_id or existing.query_id == query_id:
                _track_review_status(existing.review_status)
            if query_id and (index % 5 == 0 or index == total_candidates):
                _commit_query_progress(index)
            continue

        score = score_text(c["title"], c.get("abstract"))
        _, heuristic_recommendation = decision_from_score(score)
        review_status = "needs_review"
        decision_source = "policy_no_ai" if ai_policy_no_ai else "fallback_heuristic"
        ai_decision = None
        ai_confidence = None
        if not ai_policy_no_ai and ai_filter is not None:
            if session_context and not logged_context:
                logger.info(
                    "ai_context run_id=%s query_id=%s query_text=%s context_source=run_snapshot session_context=%s",
                    run_id,
                    query_id or "-",
                    query_text or "-",
                    session_context,
                )
                logged_context = True
            try:
                ai_result = _evaluate_ai_with_watchdog(
                    ai_filter,
                    run_id=run_id,
                    query_id=query_id,
                    title=c["title"],
                    kwargs={
                        "title": c["title"],
                        "abstract": c.get("abstract"),
                        "base_score": score,
                        "base_decision": heuristic_recommendation,
                        "session_queries": session_queries or [],
                        "session_context": session_context or "",
                    },
                )
            except TypeError:
                # Backward-compat for tests/stubs that still use the old evaluate signature.
                ai_result = _evaluate_ai_with_watchdog(
                    ai_filter,
                    run_id=run_id,
                    query_id=query_id,
                    title=c["title"],
                    kwargs={
                        "title": c["title"],
                        "abstract": c.get("abstract"),
                        "base_score": score,
                        "base_decision": heuristic_recommendation,
                    },
                )
            if ai_result is not None:
                review_status = ai_result.decision
                decision_source = "ai"
                ai_decision = ai_result.decision
                ai_confidence = ai_result.confidence
                if observability is not None:
                    observability.record_provider_call(
                        run_id=run_id,
                        iteration=iteration,
                        provider="ai_filter",
                        operation="evaluate",
                        latency_ms=0.0,
                        ok=True,
                    )
            else:
                review_status = "needs_review"
                decision_source = "fallback_heuristic"
                error_category = ai_filter.pop_last_error_category() if hasattr(ai_filter, "pop_last_error_category") else None
                if observability is not None and error_category:
                    if error_category == "auth_error":
                        observability.inc("ai_auth_error")
                    elif error_category == "rate_limited":
                        observability.inc("ai_rate_limited")
                    elif error_category == "timeout":
                        observability.inc("ai_timeout")
                    elif error_category == "provider_error":
                        observability.inc("ai_provider_error")
                    observability.record_provider_call(
                        run_id=run_id,
                        iteration=iteration,
                        provider="ai_filter",
                        operation="evaluate",
                        latency_ms=0.0,
                        ok=False,
                        error=error_category,
                    )
                runtime_warning = ai_filter.consume_runtime_warning() if hasattr(ai_filter, "consume_runtime_warning") else None
                if observability is not None and runtime_warning:
                    observability.record_provider_call(
                        run_id=run_id,
                        iteration=iteration,
                        provider="ai_filter",
                        operation="runtime_warning",
                        latency_ms=0.0,
                        ok=False,
                        error=runtime_warning,
                    )
        elif not ai_policy_no_ai and ai_filter is None:
            decision_source = "fallback_heuristic"

        accepted = review_status == "auto_accept"
        if accepted:
            stats.new_accepted_unique += 1
            if observability is not None:
                observability.inc("accepted")
        elif observability is not None:
            observability.inc("rejected")
        _track_review_status(review_status)

        source_payload = {
            "id": sid,
            "run_id": run_id,
            "title": c["title"],
            "year": normalized_year,
            "url": c.get("url"),
            "doi": c.get("doi"),
            "abstract": c.get("abstract"),
            "journal": c.get("journal"),
            "authors": list(c.get("authors") or []),
            "citation_count": c.get("citation_count"),
            "type": c["type"],
            "source": c["source"],
            "source_native_id": c.get("source_native_id"),
            "patent_office": c.get("patent_office"),
            "patent_number": c.get("patent_number"),
            "iteration": iteration,
            "discovery_method": c["discovery_method"],
            "relevance_score": score,
            "accepted": accepted,
            "review_status": review_status,
            "final_decision": review_status,
            "decision_source": decision_source,
            "heuristic_recommendation": heuristic_recommendation,
            "heuristic_score": score,
            "ai_decision": ai_decision,
            "ai_confidence": ai_confidence,
            "query_id": query_id,
            "query_step_number": query_step_number,
            "query_source_number": next_query_source_number + 1 if query_id else None,
            "parent_source_id": c.get("parent_source_id"),
            "provenance_history": [_provenance_event(c, iteration)],
        }
        inserted_sid = _insert_source_with_conflict_recovery(
            db=db,
            run_id=run_id,
            canonical_sid=canonical_sid,
            source_payload=source_payload,
        )
        if inserted_sid is None:
            if observability is not None:
                observability.inc("dedup")
            if query_id and (index % 5 == 0 or index == total_candidates):
                _commit_query_progress(index)
            continue
        if query_id:
            next_query_source_number += 1
        pending_source_ids.add(inserted_sid)
        if query_id and (index % 5 == 0 or index == total_candidates):
            _commit_query_progress(index)
    db.commit()
    return stats


def _run_scoped_source_id(db: Session, run_id: str, canonical_sid: str) -> str:
    existing = db.get(Source, canonical_sid)
    if existing is None or existing.run_id == run_id:
        return canonical_sid
    # Keep canonical ID when possible; add run scope only for cross-run PK conflicts.
    return f"{canonical_sid}::run:{run_id}"


def _insert_source_with_conflict_recovery(
    *,
    db: Session,
    run_id: str,
    canonical_sid: str,
    source_payload: dict,
) -> str | None:
    attempted_ids: list[str] = [str(source_payload["id"])]
    last_exc: IntegrityError | None = None
    idx = 0
    while idx < len(attempted_ids):
        sid = attempted_ids[idx]
        payload = dict(source_payload)
        payload["id"] = sid
        try:
            with db.begin_nested():
                db.add(Source(**payload))
                db.flush()
            return sid
        except IntegrityError as exc:
            last_exc = exc
            # Concurrent runs may claim the same canonical ID between pre-check and insert.
            if "UNIQUE constraint failed: sources.id" not in str(exc):
                raise
            existing = db.get(Source, sid)
            if existing is not None and existing.run_id == run_id:
                return None
            fresh_scoped_sid = _run_scoped_source_id(db, run_id, canonical_sid)
            if fresh_scoped_sid not in attempted_ids:
                attempted_ids.append(fresh_scoped_sid)
        idx += 1

    # One last lookup handles races where another insert in this run won the ID.
    existing = db.get(Source, attempted_ids[-1])
    if existing is not None and existing.run_id == run_id:
        return None
    if last_exc is not None:
        raise last_exc
    return None


def _find_existing_source(db: Session, run_id: str, candidate: dict, candidate_id: str) -> Source | None:
    run = db.get(Run, run_id)
    session_id, context_key = _run_session_scope(run)

    direct = db.get(Source, candidate_id)
    if direct is not None and direct.run_id == run_id:
        return direct

    doi = candidate.get("doi")
    url = candidate.get("url")
    native_id = candidate.get("source_native_id")

    def _scope_query(*conditions, allow_other_contexts: bool = False):
        stmt = select(Source).join(Run, Run.id == Source.run_id)
        if session_id:
            stmt = stmt.where(Run.session_id == session_id)
            if not allow_other_contexts:
                stmt = stmt.where(Run.session_context_key == context_key)
        else:
            stmt = stmt.where(Source.run_id == run_id)
        return stmt.where(*conditions)

    def _prefer_human(rows: list[Source]) -> Source | None:
        for row in rows:
            if row.review_status in HUMAN_REVIEW_STATUSES:
                return row
        return rows[0] if rows else None

    def _rows_for(stmt, *, limit_all: bool = False) -> list[Source]:
        rows = db.scalars(stmt).all()
        if not rows:
            return []
        if limit_all:
            return rows
        in_run_rows = [row for row in rows if row.run_id == run_id]
        if in_run_rows:
            return in_run_rows
        return rows

    if session_id:
        human_direct = db.scalars(
            _scope_query(Source.id == candidate_id, Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)), allow_other_contexts=True)
        ).all()
        preferred = _prefer_human(human_direct)
        if preferred is not None:
            return preferred

    if doi:
        if session_id:
            human_rows = db.scalars(
                _scope_query(Source.doi == doi, Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)), allow_other_contexts=True)
            ).all()
            preferred = _prefer_human(human_rows)
            if preferred is not None:
                return preferred
        rows = _rows_for(_scope_query(Source.doi == doi), limit_all=True)
        preferred = _prefer_human(rows)
        if preferred is not None:
            return preferred

    if url:
        normalized_url = canonicalize_url(url)
        if session_id:
            human_rows = db.scalars(
                _scope_query(
                    Source.url.is_not(None),
                    Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)),
                    allow_other_contexts=True,
                )
            ).all()
            for existing in human_rows:
                if existing.url and canonicalize_url(existing.url) == normalized_url:
                    return existing
        rows = _rows_for(_scope_query(Source.url.is_not(None)), limit_all=True)
        for existing in rows:
            if existing.url and canonicalize_url(existing.url) == normalized_url:
                return existing

    if native_id:
        if session_id:
            human_rows = db.scalars(
                _scope_query(
                    Source.source == candidate.get("source"),
                    Source.source_native_id == native_id,
                    Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)),
                    allow_other_contexts=True,
                )
            ).all()
            preferred = _prefer_human(human_rows)
            if preferred is not None:
                return preferred
        rows = _rows_for(
            _scope_query(
                Source.source == candidate.get("source"),
                Source.source_native_id == native_id,
            ),
            limit_all=True,
        )
        preferred = _prefer_human(rows)
        if preferred is not None:
            return preferred

    if candidate.get("year") is None:
        fuzzy_stmt = _scope_query()
    else:
        year = int(candidate["year"])
        fuzzy_stmt = _scope_query(or_(Source.year.is_(None), Source.year.between(year - 1, year + 1)))
    fuzzy_rows = _rows_for(fuzzy_stmt, limit_all=True)
    human_fuzzy_rows = []
    if session_id:
        if candidate.get("year") is None:
            human_fuzzy_stmt = _scope_query(
                Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)),
                allow_other_contexts=True,
            )
        else:
            year = int(candidate["year"])
            human_fuzzy_stmt = _scope_query(
                or_(Source.year.is_(None), Source.year.between(year - 1, year + 1)),
                Source.review_status.in_(tuple(HUMAN_REVIEW_STATUSES)),
                allow_other_contexts=True,
            )
        human_fuzzy_rows = db.scalars(human_fuzzy_stmt).all()
    for existing in human_fuzzy_rows:
        if is_fuzzy_duplicate(
            title_a=existing.title,
            year_a=existing.year,
            title_b=candidate["title"],
            year_b=candidate.get("year"),
            threshold=0.92,
        ):
            return existing
    for existing in fuzzy_rows:
        if is_fuzzy_duplicate(
            title_a=existing.title,
            year_a=existing.year,
            title_b=candidate["title"],
            year_b=candidate.get("year"),
            threshold=0.92,
        ):
            return existing
    return None


def _merge_source(target: Source, incoming: dict, *, iteration: int) -> None:
    # Keep the more complete record when dedup identifies the same source.
    if not target.abstract and incoming.get("abstract"):
        target.abstract = incoming["abstract"]
    if not target.doi and incoming.get("doi"):
        target.doi = incoming["doi"]
    if not target.url and incoming.get("url"):
        target.url = incoming["url"]
    if target.year is None and incoming.get("year") is not None:
        target.year = incoming["year"]
    if not target.journal and incoming.get("journal"):
        target.journal = incoming["journal"]
    if (not target.authors) and incoming.get("authors"):
        target.authors = list(incoming["authors"])
    if target.citation_count is None and incoming.get("citation_count") is not None:
        target.citation_count = incoming["citation_count"]
    elif incoming.get("citation_count") is not None and target.citation_count is not None:
        target.citation_count = max(int(target.citation_count), int(incoming["citation_count"]))
    if not target.source_native_id and incoming.get("source_native_id"):
        target.source_native_id = incoming["source_native_id"]
    if not target.patent_office and incoming.get("patent_office"):
        target.patent_office = incoming["patent_office"]
    if not target.patent_number and incoming.get("patent_number"):
        target.patent_number = incoming["patent_number"]
    history = list(target.provenance_history or [])
    history.append(_provenance_event(incoming, iteration))
    target.provenance_history = history
    target.updated_at = datetime.now(UTC)


def _provenance_event(candidate: dict, iteration: int) -> dict:
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "discovery_method": candidate.get("discovery_method"),
        "parent_source_id": candidate.get("parent_source_id"),
        "provider": candidate.get("source"),
        "source_native_id": candidate.get("source_native_id"),
    }


def _normalize_provider_limits(provider_limits: dict[str, int] | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    if not isinstance(provider_limits, dict):
        return normalized
    for key, max_value in (("openalex", 200), ("semantic_scholar", 100), ("brave", 20)):
        value = provider_limits.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed < 1:
            continue
        normalized[key] = min(parsed, max_value)
    return normalized


def _provider_limits_for_run(db: Session, run_id: str) -> dict[str, int]:
    rows = db.scalars(
        select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run_id).order_by(DiscoveryRunQuery.position.asc())
    ).all()
    for row in rows:
        if isinstance(row.query_metadata, dict):
            provider_limits = _normalize_provider_limits(row.query_metadata.get("provider_limits"))
            if provider_limits:
                return provider_limits
    return {}


def _query_context_metadata(
    *,
    session_id: str | None,
    session_context: str | None,
    provider_limits: dict[str, int] | None = None,
) -> dict:
    normalized_context = (session_context or "").strip()
    return {
        "session_id": (session_id or "").strip() or None,
        "session_context": normalized_context or None,
        "session_context_updated_at": datetime.now(UTC).isoformat() if normalized_context else None,
        "provider_limits": _normalize_provider_limits(provider_limits),
    }


def _count_accepted(db: Session, run_id: str) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Source).where(Source.run_id == run_id, Source.accepted.is_(True)))
        or 0
    )


def _store_keywords_for_iteration(db: Session, run_id: str, iteration: int) -> None:
    accepted_texts = db.scalars(
        select(Source.abstract).where(Source.run_id == run_id, Source.accepted.is_(True), Source.abstract.is_not(None))
    ).all()
    keywords = extract_keywords(list(accepted_texts), top_k=20)
    freqs = Counter(keywords)
    for kw, freq in freqs.items():
        db.merge(Keyword(run_id=run_id, iteration=iteration, keyword=kw, frequency=freq))
    db.commit()


def _next_iteration_queries(db: Session, run_id: str, iteration: int) -> list[str]:
    rows = db.scalars(
        select(Keyword.keyword)
        .where(Keyword.run_id == run_id, Keyword.iteration == iteration)
        .order_by(Keyword.frequency.desc(), Keyword.keyword.asc())
        .limit(20)
    ).all()
    queries = build_next_queries(rows, max_queries=10)
    if not queries:
        return ["ultrapure water semiconductor process control"]
    return queries
