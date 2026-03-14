from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..config import settings
from ..db import get_db
from ..discovery import create_run, enqueue_citation_iteration_run, enqueue_run, export_sources_raw, review_source
from ..models import DiscoveryRunQuery, Run, Source
from ..rate_limit import require_rate_limit
from ..runtime_state import request_run_stop
from ..schemas import (
    CitationIterationRequest,
    DiscoveryRunQueriesResponse,
    DiscoveryRunQueryOut,
    RunCreateRequest,
    RunCreateResponse,
    SourceReviewRequest,
    SourceReviewResponse,
)

router = APIRouter(tags=["discovery"])
logger = logging.getLogger("knowledge_miner")


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


@router.post("/v1/discovery/runs", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_discovery_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    selected_queries = payload.selected_queries or payload.seed_queries
    try:
        run = create_run(db, selected_queries, 1, ai_filter_enabled=payload.ai_filter_enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="selected_queries_required") from exc
    _enqueue_discovery_task(background_tasks, run.id)
    return RunCreateResponse(run_id=run.id, status=run.status)


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
    accepted_count = db.scalar(
        select(func.count()).select_from(Source).where(Source.run_id == run_id, Source.accepted.is_(True))
    ) or 0
    if accepted_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Need at least 1 accepted paper before running citation iteration.",
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
        db.commit()
        return {"run_id": run.id, "status": run.status, "message": "Discovery run stopped."}

    query = db.scalars(
        select(DiscoveryRunQuery)
        .where(DiscoveryRunQuery.run_id == run_id)
        .order_by(DiscoveryRunQuery.position.desc())
        .limit(1)
    ).first()
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
    return DiscoveryRunQueriesResponse(
        run_id=run_id,
        queries=[
            DiscoveryRunQueryOut(
                query=row.query_text,
                position=row.position,
                status=row.status,
                discovered_count=row.discovered_count,
                openalex_count=row.openalex_count,
                brave_count=row.brave_count,
                semantic_scholar_count=row.semantic_scholar_count,
                accepted_count=row.accepted_count,
                rejected_count=row.rejected_count,
                pending_count=row.pending_count,
                processing_count=row.processing_count,
                scope_total_parents=row.scope_total_parents,
                scope_processed_parents=row.scope_processed_parents,
                checkpoint_state=row.checkpoint_state,
                error_message=row.error_message,
            )
            for row in rows
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
