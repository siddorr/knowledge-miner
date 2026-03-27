from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..ai_filter import AIAuthError, AIProviderError, AIRateLimitError, AITimeoutError, generate_query_suggestions
from ..config import settings
from ..db import get_db
from ..discovery import (
    create_run,
    enqueue_bookmark_seed_run,
    enqueue_citation_iteration_run,
    enqueue_run,
    export_sources_raw,
    review_source,
    session_citation_parent_ids,
    session_citation_parent_total,
)
from ..models import CitationExpansionParent, DiscoveryCitationSeed, DiscoveryRunQuery, Run, Source
from ..rate_limit import require_rate_limit
from ..runtime_state import clear_run_stop_request, request_run_stop
from ..schemas import (
    CitationIterationRequest,
    QuerySuggestionsRequest,
    QuerySuggestionsResponse,
    DiscoveryRunQueriesResponse,
    DiscoveryRunQueryOut,
    SessionDiscoveryQueriesResponse,
    RunCreateRequest,
    RunCreateResponse,
    SourceReviewRequest,
    SourceReviewResponse,
)

router = APIRouter(tags=["discovery"])
logger = logging.getLogger("knowledge_miner")
_FORCE_STOP_STALE_AFTER = timedelta(seconds=60)


def _as_utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _unexpanded_accepted_parent_count(db: Session, run_id: str) -> int:
    run = db.get(Run, run_id)
    if run is None:
        return 0
    return len(session_citation_parent_ids(db, target_run_id=run_id, session_id=run.session_id))


def _latest_citation_query(db: Session, run_id: str) -> DiscoveryRunQuery | None:
    return db.scalars(
        select(DiscoveryRunQuery)
        .where(
            DiscoveryRunQuery.run_id == run_id,
            DiscoveryRunQuery.query_text == "citation expansion",
        )
        .order_by(DiscoveryRunQuery.position.desc())
        .limit(1)
    ).first()


def _repair_stale_query_state(run: Run, row: DiscoveryRunQuery) -> bool:
    changed = False
    if run.status == "failed" and row.status in {"searching", "ranking_relevance"}:
        row.status = "failed"
        changed = True
        if row.query_text == "citation expansion" and row.checkpoint_state == "running":
            row.checkpoint_state = "resumable"
            changed = True
        if not row.error_message and run.error_message:
            row.error_message = run.error_message
            changed = True
    elif run.status == "completed" and row.status in {"searching", "ranking_relevance"}:
        row.status = "completed"
        changed = True
        if row.query_text == "citation expansion" and row.checkpoint_state == "running":
            row.checkpoint_state = "completed"
            changed = True
    return changed


def _repair_stale_run_queries(db: Session, run: Run, rows: list[DiscoveryRunQuery]) -> None:
    changed = False
    for row in rows:
        changed = _repair_stale_query_state(run, row) or changed
    if changed:
        db.commit()


def _live_query_review_counts(db: Session, row: DiscoveryRunQuery) -> tuple[int, int, int, int]:
    statuses = db.scalars(
        select(Source.review_status).where(
            Source.run_id == row.run_id,
            Source.query_id == row.id,
        )
    ).all()
    accepted = sum(1 for status in statuses if status in {"auto_accept", "human_accept"})
    rejected = sum(1 for status in statuses if status in {"auto_reject", "human_reject"})
    pending = sum(1 for status in statuses if status not in {"auto_accept", "human_accept", "auto_reject", "human_reject", "processing"})
    processing = 0 if row.status in {"completed", "failed"} else int(row.processing_count or 0)
    return accepted, rejected, pending, processing


def _not_found_diagnostics(db: Session, *, run_id: str | None = None, source_id: str | None = None) -> dict:
    run_count = db.scalar(select(func.count()).select_from(Run)) or 0
    source_count = db.scalar(select(func.count()).select_from(Source)) or 0
    latest_run_ids = db.scalars(select(Run.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(5)).all()
    latest_source_ids = db.scalars(select(Source.id).order_by(Source.created_at.desc(), Source.id.desc()).limit(5)).all()
    has_run = bool(run_id and db.get(Run, run_id))
    has_source = bool(source_id and db.get(Source, source_id))
    return {
        "run_count": int(run_count),
        "source_count": int(source_count),
        "latest_run_ids": latest_run_ids,
        "latest_source_ids": latest_source_ids,
        "requested_run_id": run_id,
        "requested_source_id": source_id,
        "requested_run_exists": has_run,
        "requested_source_exists": has_source,
    }


def _enqueue_discovery_task(background_tasks: BackgroundTasks, run_id: str) -> None:
    try:
        from .. import main as main_module
        enqueue_fn = getattr(main_module, "enqueue_run", enqueue_run)
    except Exception:
        enqueue_fn = enqueue_run
    background_tasks.add_task(enqueue_fn, run_id)


def _enqueue_citation_task(background_tasks: BackgroundTasks, run_id: str, source_run_id: str) -> None:
    try:
        from .. import main as main_module
        enqueue_fn = getattr(main_module, "enqueue_citation_iteration_run", enqueue_citation_iteration_run)
    except Exception:
        enqueue_fn = enqueue_citation_iteration_run
    background_tasks.add_task(enqueue_fn, run_id, source_run_id=source_run_id)


def _enqueue_bookmark_seed_task(background_tasks: BackgroundTasks, run_id: str) -> None:
    try:
        from .. import main as main_module
        enqueue_fn = getattr(main_module, "enqueue_bookmark_seed_run", enqueue_bookmark_seed_run)
    except Exception:
        enqueue_fn = enqueue_bookmark_seed_run
    background_tasks.add_task(enqueue_fn, run_id)


@router.post("/v1/discovery/runs", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_discovery_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    selected_queries = payload.selected_queries or payload.seed_queries
    normalized_session_id = payload.session_id.strip()
    normalized_session_context = payload.session_context.strip()
    if not normalized_session_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id_required")
    if not normalized_session_context:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_context_required")
    try:
        run = create_run(
            db,
            selected_queries,
            1,
            session_id=normalized_session_id,
            session_context=normalized_session_context,
            ai_filter_enabled=payload.ai_filter_enabled,
            provider_limits=payload.provider_limits.model_dump(exclude_none=True) if payload.provider_limits else None,
        )
    except ValueError as exc:
        detail = str(exc) or "invalid_request"
        if detail not in {"selected_queries_required", "seed_queries_required", "session_context_required", "session_id_required"}:
            detail = "invalid_request"
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from exc
    _enqueue_discovery_task(background_tasks, run.id)
    return RunCreateResponse(run_id=run.id, status=run.status)


@router.post("/v1/discovery/query-suggestions", response_model=QuerySuggestionsResponse)
def suggest_discovery_queries(
    payload: QuerySuggestionsRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> QuerySuggestionsResponse:
    try:
        suggestions = generate_query_suggestions(
            session_context=payload.session_context.strip(),
            existing_queries=[q.strip() for q in payload.existing_queries if str(q).strip()],
            max_suggestions=payload.max_suggestions,
        )
    except AIAuthError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except AIRateLimitError as exc:
        logger.warning("query_suggestions_failed reason=%s", str(exc))
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except (AITimeoutError, AIProviderError, ValueError) as exc:
        logger.warning("query_suggestions_failed reason=%s", str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    logger.info("query_suggestions_completed count=%s", len(suggestions))
    if not suggestions:
        return QuerySuggestionsResponse(suggestions=[], source="ai", warning="No suggestions returned.")
    return QuerySuggestionsResponse(suggestions=suggestions, source="ai", warning=None)


@router.post("/v1/discovery/runs/{run_id}/next-citation-iteration", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_citation_iteration_run(
    run_id: str,
    payload: CitationIterationRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    previous = db.get(Run, run_id)
    if previous is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    if previous.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_already_running")
    accepted_count = session_citation_parent_total(db, target_run_id=run_id, session_id=previous.session_id)
    if accepted_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Need at least 1 accepted paper before running citation expansion.",
        )
    remaining_parent_count = _unexpanded_accepted_parent_count(db, run_id)
    if remaining_parent_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No new accepted papers are available for citation expansion.",
        )
    latest_citation_query = _latest_citation_query(db, previous.id)
    if latest_citation_query is not None and latest_citation_query.checkpoint_state in {"running", "resumable"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="citation_iteration_resumable_exists_use_resume",
        )
    _enqueue_citation_task(background_tasks, previous.id, previous.id)
    return RunCreateResponse(run_id=previous.id, status=previous.status)


@router.post("/v1/discovery/runs/{run_id}/citation-expansion/resume", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def resume_citation_iteration_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    run = db.get(Run, run_id)
    if run is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    if run.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_already_running")
    has_bookmark_seed = db.scalars(
        select(DiscoveryCitationSeed).where(DiscoveryCitationSeed.run_id == run.id).limit(1)
    ).first()
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run.id)
    clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run.id)
    if has_bookmark_seed is not None:
        _enqueue_bookmark_seed_task(background_tasks, run.id)
    else:
        _enqueue_citation_task(background_tasks, run.id, run.id)
    return RunCreateResponse(run_id=run.id, status=run.status)


@router.post("/v1/discovery/runs/{run_id}/stop")
def stop_discovery_run(
    run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    if run.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_running")
    if run.status == "queued":
        run.status = "failed"
        run.error_message = "stopped_by_user"
        latest_query = _latest_citation_query(db, run_id)
        if latest_query is not None:
            latest_query.status = "failed"
            latest_query.error_message = "stopped_by_user"
            if latest_query.query_text == "citation expansion":
                latest_query.checkpoint_state = "resumable"
        db.commit()
        return {"run_id": run.id, "status": run.status, "message": "Discovery run stopped."}

    query = db.scalars(
        select(DiscoveryRunQuery)
        .where(DiscoveryRunQuery.run_id == run_id)
        .order_by(DiscoveryRunQuery.position.desc())
        .limit(1)
    ).first()
    latest_activity = _as_utc_timestamp(run.updated_at) or _as_utc_timestamp(run.created_at)
    query_updated_at = _as_utc_timestamp(query.updated_at) if query is not None else None
    if latest_activity is None:
        latest_activity = query_updated_at
    elif query_updated_at is not None:
        latest_activity = max(latest_activity, query_updated_at)
    if latest_activity and (datetime.now(UTC) - latest_activity) >= _FORCE_STOP_STALE_AFTER:
        run.status = "failed"
        run.error_message = "stopped_by_user"
        run.updated_at = datetime.now(UTC)
        if query is not None:
            query.status = "failed"
            query.error_message = "stopped_by_user"
            query.updated_at = datetime.now(UTC)
            if (query.query_text or "").strip() == "citation expansion":
                query.checkpoint_state = "resumable"
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery_citation", run_id=run_id)
        clear_run_stop_request(base_dir=settings.runtime_state_dir, phase="discovery", run_id=run_id)
        db.commit()
        return {"run_id": run.id, "status": run.status, "message": "Discovery run force-stopped."}
    phase = "discovery_citation" if query and query.query_text == "citation expansion" else "discovery"
    request_run_stop(base_dir=settings.runtime_state_dir, phase=phase, run_id=run_id)
    return {"run_id": run.id, "status": run.status, "message": "Stop requested."}


@router.get("/v1/discovery/runs/{run_id}/queries", response_model=DiscoveryRunQueriesResponse)
def list_discovery_run_queries(
    run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> DiscoveryRunQueriesResponse:
    run = db.get(Run, run_id)
    if run is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    rows = db.scalars(
        select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run_id).order_by(DiscoveryRunQuery.position.asc())
    ).all()
    _repair_stale_run_queries(db, run, rows)
    run_number = None
    if run.session_id:
        ordered_run_ids = db.scalars(
            select(Run.id).where(Run.session_id == run.session_id).order_by(Run.created_at.asc(), Run.id.asc())
        ).all()
        run_numbers = {candidate_run_id: index + 1 for index, candidate_run_id in enumerate(ordered_run_ids)}
        run_number = run_numbers.get(run.id)
    return DiscoveryRunQueriesResponse(
        run_id=run_id,
        queries=[
            (
                lambda counts: DiscoveryRunQueryOut(
                    run_id=run_id,
                    run_number=run_number,
                    query_step_number=row.position,
                    query_lineage_number=(f"{run_number}.{row.position}" if run_number is not None else None),
                    query=row.query_text,
                    position=row.position,
                    status=row.status,
                    discovered_count=row.discovered_count,
                    openalex_count=row.openalex_count,
                    brave_count=row.brave_count,
                    semantic_scholar_count=row.semantic_scholar_count,
                    openalex_status=row.openalex_status,
                    semantic_scholar_status=row.semantic_scholar_status,
                    brave_status=row.brave_status,
                    openalex_error_message=row.openalex_error_message,
                    semantic_scholar_error_message=row.semantic_scholar_error_message,
                    brave_error_message=row.brave_error_message,
                    accepted_count=counts[0],
                    rejected_count=counts[1],
                    pending_count=counts[2],
                    processing_count=counts[3],
                    scope_total_parents=row.scope_total_parents,
                    scope_processed_parents=row.scope_processed_parents,
                    checkpoint_state=row.checkpoint_state,
                    has_session_context=bool(isinstance(row.query_metadata, dict) and row.query_metadata.get("session_context")),
                    session_context_preview=(
                        str(row.query_metadata.get("session_context"))[:120]
                        if isinstance(row.query_metadata, dict) and row.query_metadata.get("session_context")
                        else None
                    ),
                    error_message=row.error_message,
                )
            )(_live_query_review_counts(db, row))
            for row in rows
        ],
    )


@router.get("/v1/sessions/{session_id}/queries", response_model=SessionDiscoveryQueriesResponse)
def list_session_discovery_queries(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionDiscoveryQueriesResponse:
    ordered_run_ids = db.scalars(
        select(Run.id).where(Run.session_id == session_id).order_by(Run.created_at.asc(), Run.id.asc())
    ).all()
    run_numbers = {run_id: index + 1 for index, run_id in enumerate(ordered_run_ids)}
    rows = db.execute(
        select(DiscoveryRunQuery, Run)
        .join(Run, Run.id == DiscoveryRunQuery.run_id)
        .where(Run.session_id == session_id)
        .order_by(Run.created_at.desc(), Run.id.desc(), DiscoveryRunQuery.position.asc())
    ).all()
    runs_by_id: dict[str, Run] = {}
    query_rows_by_run: dict[str, list[DiscoveryRunQuery]] = {}
    for row, run in rows:
        runs_by_id[run.id] = run
        query_rows_by_run.setdefault(run.id, []).append(row)
    for run_id, query_rows in query_rows_by_run.items():
        _repair_stale_run_queries(db, runs_by_id[run_id], query_rows)
    return SessionDiscoveryQueriesResponse(
        session_id=session_id,
        queries=[
            (
                lambda counts: DiscoveryRunQueryOut(
                    run_id=run.id,
                    run_number=run_numbers.get(run.id),
                    query_step_number=row.position,
                    query_lineage_number=(
                        f"{run_numbers.get(run.id)}.{row.position}"
                        if run_numbers.get(run.id) is not None
                        else None
                    ),
                    query=row.query_text,
                    position=row.position,
                    status=row.status,
                    discovered_count=row.discovered_count,
                    openalex_count=row.openalex_count,
                    brave_count=row.brave_count,
                    semantic_scholar_count=row.semantic_scholar_count,
                    openalex_status=row.openalex_status,
                    semantic_scholar_status=row.semantic_scholar_status,
                    brave_status=row.brave_status,
                    openalex_error_message=row.openalex_error_message,
                    semantic_scholar_error_message=row.semantic_scholar_error_message,
                    brave_error_message=row.brave_error_message,
                    accepted_count=counts[0],
                    rejected_count=counts[1],
                    pending_count=counts[2],
                    processing_count=counts[3],
                    scope_total_parents=row.scope_total_parents,
                    scope_processed_parents=row.scope_processed_parents,
                    checkpoint_state=row.checkpoint_state,
                    has_session_context=bool(isinstance(row.query_metadata, dict) and row.query_metadata.get("session_context")),
                    session_context_preview=(
                        str(row.query_metadata.get("session_context"))[:120]
                        if isinstance(row.query_metadata, dict) and row.query_metadata.get("session_context")
                        else None
                    ),
                    error_message=row.error_message,
                )
            )(_live_query_review_counts(db, row))
            for row, run in rows
        ],
    )


@router.post("/v1/sources/{source_id:path}/review", response_model=SourceReviewResponse)
def source_review_endpoint(
    source_id: str,
    payload: SourceReviewRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SourceReviewResponse:
    source = db.get(Source, source_id)
    if source is None:
        logger.warning("source_not_found %s", _not_found_diagnostics(db, source_id=source_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="source_not_found; hint=reload_review_queue_or_check_discovery_run_context",
        )
    if payload.run_id and payload.run_id != source.run_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_context_mismatch")
    try:
        updated = review_source(db, source, payload.decision)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from exc
    return SourceReviewResponse(source_id=updated.id, accepted=updated.accepted, decision_source="human_review")


@router.get("/v1/exports/sources_raw")
def export_sources(
    run_id: str = Query(..., min_length=1),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    if run.status != "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete")
    path = export_sources_raw(db, run_id)
    return FileResponse(path=path, media_type="application/json", filename="sources_raw.json")
