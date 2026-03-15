from __future__ import annotations

from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
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
from .db import SessionLocal
from .dedup import canonical_id, canonicalize_url, is_fuzzy_duplicate
from .iteration import build_next_queries, extract_keywords
from .models import CitationEdge, CitationExpansionParent, DiscoveryRunQuery, Keyword, Run, Source
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
    normalized_session_context = (session_context or "").strip()
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
    db.refresh(run)
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


def _execute_run_with_lock(run_id: str, run_lock: Path) -> None:
    try:
        execute_run_by_id(run_id)
    finally:
        release_run_lock(run_lock)


def _execute_citation_run_with_lock(run_id: str, source_run_id: str, run_lock: Path) -> None:
    try:
        execute_citation_iteration_run_by_id(run_id, source_run_id=source_run_id)
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
        query_row.updated_at = datetime.now(UTC)
        db.commit()

        checkpoint = _load_checkpoint(run_id, query_row.id) or {}
        parent_ids: list[str]
        processed_parents = int(checkpoint.get("processed_parents", 0))
        if isinstance(checkpoint.get("remaining_parent_ids"), list):
            parent_ids = [str(value) for value in checkpoint["remaining_parent_ids"]]
        else:
            parent_ids = [
                source.id
                for source in db.scalars(
                    select(Source)
                    .where(Source.run_id == source_run_id, Source.accepted.is_(True))
                    .order_by(Source.relevance_score.desc(), Source.id.asc())
                ).all()
                if db.get(CitationExpansionParent, (run_id, source.id)) is None
            ]
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
                _save_checkpoint(
                    run_id,
                    query_row.id,
                    {
                        "processed_parents": processed_parents,
                        "remaining_parent_ids": parent_ids[index + 1 :],
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                )
                db.commit()
                continue
            query_row.status = "searching"
            query_row.checkpoint_state = "running"
            query_row.updated_at = datetime.now(UTC)
            db.commit()

            citation_candidates, citation_edges = _expand_citations_for_parent_unbounded(
                run_id=run_id,
                parent=parent,
                connector=connectors_by_name.get(parent.source),
                observability=observability,
                iteration=max(int(run.current_iteration or 1), 1),
            )
            provider_counts = Counter((c.get("source") or "").strip().lower() for c in citation_candidates)
            query_row.discovered_count = int(query_row.discovered_count) + len(citation_candidates)
            query_row.openalex_count = int(query_row.openalex_count) + int(provider_counts.get("openalex", 0))
            query_row.brave_count = int(query_row.brave_count) + int(provider_counts.get("brave", 0))
            query_row.semantic_scholar_count = int(query_row.semantic_scholar_count) + int(
                provider_counts.get("semantic_scholar", 0)
            )
            query_row.status = "ranking_relevance"
            query_row.processing_count = len(citation_candidates)
            query_row.updated_at = datetime.now(UTC)
            db.commit()

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
            query_row.accepted_count = int(query_row.accepted_count) + stats.accepted
            query_row.rejected_count = int(query_row.rejected_count) + stats.rejected
            query_row.pending_count = int(query_row.pending_count) + stats.pending
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
                    query_id=query_row.id,
                )
            )
            processed_parents += 1
            query_row.scope_processed_parents = processed_parents
            query_row.updated_at = datetime.now(UTC)
            _save_checkpoint(
                run_id,
                query_row.id,
                {
                    "processed_parents": processed_parents,
                    "remaining_parent_ids": parent_ids[index + 1 :],
                    "updated_at": datetime.now(UTC).isoformat(),
                },
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
                continue
            successful_connector_calls += 1
            observability.inc("fetched", len(rows))
            provider_counts[connector.name] += len(rows)
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


def _expand_citations_for_parent_unbounded(
    *,
    run_id: str,
    parent: Source,
    connector: Connector | None,
    observability: RunObservability,
    iteration: int,
) -> tuple[list[dict], list[tuple[str, str, str]]]:
    if connector is None:
        return [], []
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
        return [], []
    combined = list(backward) + list(forward)
    observability.inc("fetched", len(combined))
    edges: list[tuple[str, str, str]] = []
    for c in backward:
        edges.append((parent.id, _candidate_target_id(c), "cites"))
    for c in forward:
        edges.append((parent.id, _candidate_target_id(c), "cited_by"))
    return combined, edges


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
    pending_source_ids: set[str] = set()
    logged_context = False

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

    for c in candidates:
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
            _inc("pending")
            continue

        existing = _find_existing_source(db, run_id, c, canonical_sid)
        if existing is not None:
            _merge_source(existing, c, iteration=iteration)
            db.add(existing)
            if observability is not None:
                observability.inc("dedup")
            _track_review_status(existing.review_status)
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
                ai_result = ai_filter.evaluate(
                    title=c["title"],
                    abstract=c.get("abstract"),
                    base_score=score,
                    base_decision=heuristic_recommendation,
                    session_queries=session_queries or [],
                    session_context=session_context or "",
                )
            except TypeError:
                # Backward-compat for tests/stubs that still use the old evaluate signature.
                ai_result = ai_filter.evaluate(
                    title=c["title"],
                    abstract=c.get("abstract"),
                    base_score=score,
                    base_decision=heuristic_recommendation,
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
            continue
        pending_source_ids.add(inserted_sid)
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
    direct = db.get(Source, candidate_id)
    if direct is not None and direct.run_id == run_id:
        return direct

    doi = candidate.get("doi")
    url = candidate.get("url")
    native_id = candidate.get("source_native_id")

    if doi:
        row = db.scalars(select(Source).where(Source.run_id == run_id, Source.doi == doi)).first()
        if row is not None:
            return row

    if url:
        normalized_url = canonicalize_url(url)
        row = db.scalars(
            select(Source).where(
                Source.run_id == run_id,
                Source.url.is_not(None),
            )
        ).all()
        for existing in row:
            if existing.url and canonicalize_url(existing.url) == normalized_url:
                return existing

    if native_id:
        row = db.scalars(
            select(Source).where(
                Source.run_id == run_id,
                Source.source == candidate.get("source"),
                Source.source_native_id == native_id,
            )
        ).first()
        if row is not None:
            return row

    if candidate.get("year") is None:
        fuzzy_stmt = select(Source).where(Source.run_id == run_id)
    else:
        year = int(candidate["year"])
        fuzzy_stmt = select(Source).where(
            and_(Source.run_id == run_id, or_(Source.year.is_(None), Source.year.between(year - 1, year + 1)))
        )
    fuzzy_rows = db.scalars(fuzzy_stmt).all()
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
