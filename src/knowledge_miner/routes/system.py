from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..config import settings
from ..db import get_db
from ..models import AcquisitionItem, AcquisitionRun, ParseRun, ParsedDocument, Run, Source
from ..rate_limit import require_rate_limit
from ..schemas import WorkQueueItemOut, WorkQueueResponse

router = APIRouter(tags=["system"])


@router.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _reason_text(reason_code: str | None, status_value: str | None) -> str | None:
    if reason_code == "paywalled":
        return "Source appears paywalled; manual or alternate legal source required."
    if reason_code == "no_oa_found":
        return "No open-access source found from legal resolution chain."
    if reason_code == "rate_limited":
        return "Provider was rate limited; retry later."
    if reason_code == "robots_blocked":
        return "Blocked by robots or legal policy."
    if reason_code == "source_error":
        return "Source retrieval failed due to provider/network response."
    if status_value == "needs_review":
        return "AI/heuristic requires human relevance decision."
    return None


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


@router.get("/v1/runs/latest")
def get_latest_runs(
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    discovery = db.scalars(select(Run.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(1)).first()
    acquisition = db.scalars(select(AcquisitionRun.id).order_by(AcquisitionRun.created_at.desc(), AcquisitionRun.id.desc()).limit(1)).first()
    parse = db.scalars(select(ParseRun.id).order_by(ParseRun.created_at.desc(), ParseRun.id.desc()).limit(1)).first()
    return {
        "discovery_run_id": discovery,
        "acquisition_run_id": acquisition,
        "parse_run_id": parse,
    }


@router.get("/v1/work-queue", response_model=WorkQueueResponse)
def get_work_queue(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> WorkQueueResponse:
    rows: list[WorkQueueItemOut] = []

    sources = db.scalars(
        select(Source).where(Source.review_status == "needs_review").order_by(Source.updated_at.desc(), Source.id.asc())
    ).all()
    for source in sources:
        rows.append(
            WorkQueueItemOut(
                item_type="source_review",
                phase="discovery",
                run_id=source.run_id,
                source_id=source.id,
                status=source.review_status,
                title=source.title,
                reason_code="needs_review",
                reason_text=_reason_text("needs_review", source.review_status),
                context={"discovery_run_id": source.run_id, "source_id": source.id},
            )
        )

    acq_rows = db.scalars(
        select(AcquisitionItem)
        .where(AcquisitionItem.status.in_(("failed", "partial")))
        .order_by(AcquisitionItem.updated_at.desc(), AcquisitionItem.id.asc())
    ).all()
    for item in acq_rows:
        source = db.get(Source, item.source_id)
        run = db.get(AcquisitionRun, item.acq_run_id)
        rows.append(
            WorkQueueItemOut(
                item_type="acquisition_issue",
                phase="acquisition",
                run_id=item.acq_run_id,
                source_id=item.source_id,
                item_id=item.id,
                status=item.status,
                title=source.title if source is not None else item.source_id,
                reason_code=item.reason_code or "source_error",
                reason_text=_reason_text(item.reason_code, item.status),
                context={
                    "acq_run_id": item.acq_run_id,
                    "discovery_run_id": run.discovery_run_id if run is not None else None,
                    "source_id": item.source_id,
                },
            )
        )

    parsed_rows = db.scalars(
        select(ParsedDocument).where(ParsedDocument.status == "failed").order_by(ParsedDocument.updated_at.desc(), ParsedDocument.id.asc())
    ).all()
    for doc in parsed_rows:
        parse_run = db.get(ParseRun, doc.parse_run_id)
        rows.append(
            WorkQueueItemOut(
                item_type="parse_issue",
                phase="parse",
                run_id=doc.parse_run_id,
                source_id=doc.source_id,
                item_id=doc.id,
                status=doc.status,
                title=doc.title,
                reason_code="source_error",
                reason_text=_reason_text("source_error", doc.status),
                context={
                    "parse_run_id": doc.parse_run_id,
                    "acq_run_id": parse_run.acq_run_id if parse_run is not None else None,
                    "source_id": doc.source_id,
                    "document_id": doc.id,
                },
            )
        )

    page = rows[offset : offset + limit]
    return WorkQueueResponse(items=page, total=len(rows), limit=limit, offset=offset)


@router.get("/v1/debug/db-context")
def debug_db_context(
    run_id: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    if not settings.enable_debug_endpoints:
        from fastapi import HTTPException, status  # lazy import to avoid expanding top-level imports
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return _not_found_diagnostics(db, run_id=run_id, source_id=source_id)
