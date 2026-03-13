from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..discovery import create_run, enqueue_run, export_sources_raw, review_source
from ..iteration import build_next_queries, extract_keywords
from ..models import Run, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    CitationIterationRequest,
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


def _build_citation_iteration_queries(db: Session, run_id: str) -> list[str]:
    rows = db.scalars(
        select(Source).where(Source.run_id == run_id, Source.accepted.is_(True)).order_by(Source.relevance_score.desc(), Source.id.asc()).limit(200)
    ).all()
    texts: list[str] = []
    for row in rows:
        if row.title:
            texts.append(row.title)
        if row.abstract:
            texts.append(row.abstract)
    keywords = extract_keywords(texts, top_k=20)
    queries = build_next_queries(keywords, max_queries=10)
    if queries:
        return queries
    previous = db.get(Run, run_id)
    if previous and previous.seed_queries:
        return list(previous.seed_queries[:5])
    return ["ultrapure water semiconductor process control"]


def _enqueue_discovery_task(background_tasks: BackgroundTasks, run_id: str) -> None:
    try:
        from .. import main as main_module
        enqueue_fn = getattr(main_module, "enqueue_run", enqueue_run)
    except Exception:
        enqueue_fn = enqueue_run
    background_tasks.add_task(enqueue_fn, run_id)


@router.post("/v1/discovery/runs", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_discovery_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    run = create_run(db, payload.seed_queries, 1, ai_filter_enabled=payload.ai_filter_enabled)
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
    queries = _build_citation_iteration_queries(db, run_id)
    ai_filter_enabled = previous.ai_filter_active if payload.ai_filter_enabled is None else payload.ai_filter_enabled
    run = create_run(db, queries, 1, ai_filter_enabled=ai_filter_enabled)
    _enqueue_discovery_task(background_tasks, run.id)
    return RunCreateResponse(run_id=run.id, status=run.status)


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
