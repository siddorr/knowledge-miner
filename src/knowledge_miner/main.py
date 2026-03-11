from __future__ import annotations

import logging

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .acquisition import build_manifest_payload, create_acquisition_run, enqueue_acquisition_run
from .auth import require_api_key
from .config import is_sqlite_url, settings
from .db import Base, engine, get_db
from .discovery import create_run, enqueue_run, export_sources_raw, review_source
from .models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source
from .rate_limit import require_rate_limit
from .schemas import (
    AcquisitionItemsListResponse,
    AcquisitionItemOut,
    AcquisitionManifestResponse,
    AcquisitionRunCreateRequest,
    AcquisitionRunCreateResponse,
    AcquisitionRunStatusResponse,
    ArtifactOut,
    RunCreateRequest,
    RunCreateResponse,
    RunStatusResponse,
    SourceOut,
    SourceReviewRequest,
    SourceReviewResponse,
    SourcesListResponse,
)

app = FastAPI(title="UPW Literature Discovery Engine", version="0.1.0")
logger = logging.getLogger("knowledge_miner")

# Create tables on module load for v1 local/dev simplicity.
Base.metadata.create_all(bind=engine)


@app.on_event("startup")
def validate_runtime_config() -> None:
    if settings.app_env.lower() in {"production", "prod"} and is_sqlite_url(settings.database_url):
        logger.warning(
            "Production mode is configured with SQLite. Use PostgreSQL DATABASE_URL for v1 production baseline."
        )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/discovery/runs", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_discovery_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    run = create_run(db, payload.seed_queries, payload.max_iterations)
    background_tasks.add_task(enqueue_run, run.id)
    return RunCreateResponse(run_id=run.id, status=run.status)


@app.get("/v1/discovery/runs/{run_id}", response_model=RunStatusResponse)
def get_run_status(
    run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunStatusResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    return RunStatusResponse(
        run_id=run.id,
        status=run.status,
        current_iteration=run.current_iteration,
        accepted_total=run.accepted_total,
        expanded_candidates_total=run.expanded_candidates_total,
        citation_edges_total=run.citation_edges_total,
        ai_filter_active=run.ai_filter_active,
        ai_filter_warning=run.ai_filter_warning,
        new_accept_rate=float(run.new_accept_rate) if run.new_accept_rate is not None else None,
    )


@app.get("/v1/discovery/runs/{run_id}/sources", response_model=SourcesListResponse)
def list_sources(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None),
    min_score: float | None = Query(default=None, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SourcesListResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")

    stmt = select(Source).where(Source.run_id == run_id, Source.accepted.is_(True))
    if type is not None:
        stmt = stmt.where(Source.type == type)
    if min_score is not None:
        stmt = stmt.where(Source.relevance_score >= min_score)

    all_rows = db.scalars(stmt.order_by(Source.relevance_score.desc(), Source.id.asc())).all()
    page = all_rows[offset : offset + limit]
    return SourcesListResponse(
        items=[
            SourceOut(
                id=s.id,
                title=s.title,
                year=s.year,
                url=s.url,
                doi=s.doi,
                abstract=s.abstract,
                type=s.type,
                source=s.source,
                iteration=s.iteration,
                discovery_method=s.discovery_method,
                relevance_score=float(s.relevance_score),
                accepted=s.accepted,
                review_status=s.review_status,
                parent_source=s.parent_source_id,
            )
            for s in page
        ],
        total=len(all_rows),
        limit=limit,
        offset=offset,
    )


@app.post("/v1/sources/{source_id}/review", response_model=SourceReviewResponse)
def source_review(
    source_id: str,
    payload: SourceReviewRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SourceReviewResponse:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source_not_found")
    try:
        updated = review_source(db, source, payload.decision)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from exc
    return SourceReviewResponse(source_id=updated.id, accepted=updated.accepted, decision_source="human_review")


@app.get("/v1/exports/sources_raw")
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


@app.post("/v1/acquisition/runs", response_model=AcquisitionRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_acq_run(
    payload: AcquisitionRunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionRunCreateResponse:
    try:
        run = create_acquisition_run(db, payload.run_id, retry_failed_only=payload.retry_failed_only)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete") from exc

    background_tasks.add_task(enqueue_acquisition_run, run.id)
    return AcquisitionRunCreateResponse(acq_run_id=run.id, status=run.status)


@app.get("/v1/acquisition/runs/{acq_run_id}", response_model=AcquisitionRunStatusResponse)
def get_acq_run_status(
    acq_run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionRunStatusResponse:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    return AcquisitionRunStatusResponse(
        acq_run_id=run.id,
        discovery_run_id=run.discovery_run_id,
        retry_failed_only=run.retry_failed_only,
        status=run.status,
        total_sources=run.total_sources,
        downloaded_total=run.downloaded_total,
        partial_total=run.partial_total,
        failed_total=run.failed_total,
        skipped_total=run.skipped_total,
        error_message=run.error_message,
    )


@app.get("/v1/acquisition/runs/{acq_run_id}/items", response_model=AcquisitionItemsListResponse)
def list_acq_items(
    acq_run_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionItemsListResponse:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")

    rows = db.scalars(
        select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id).order_by(AcquisitionItem.source_id.asc())
    ).all()
    page = rows[offset : offset + limit]
    return AcquisitionItemsListResponse(
        items=[
            AcquisitionItemOut(
                item_id=i.id,
                source_id=i.source_id,
                status=i.status,
                attempt_count=i.attempt_count,
                selected_url=i.selected_url,
                last_error=i.last_error,
            )
            for i in page
        ],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@app.get("/v1/acquisition/artifacts/{artifact_id}", response_model=ArtifactOut)
def get_artifact(
    artifact_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ArtifactOut:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact_not_found")
    return ArtifactOut(
        artifact_id=artifact.id,
        acq_run_id=artifact.acq_run_id,
        source_id=artifact.source_id,
        item_id=artifact.item_id,
        kind=artifact.kind,
        path=artifact.path,
        checksum_sha256=artifact.checksum_sha256,
        size_bytes=artifact.size_bytes,
        mime_type=artifact.mime_type,
    )


@app.get("/v1/acquisition/runs/{acq_run_id}/manifest", response_model=AcquisitionManifestResponse)
def get_acq_manifest(
    acq_run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionManifestResponse:
    try:
        payload = build_manifest_payload(db, acq_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
    return AcquisitionManifestResponse(**payload)
