from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..acquisition import (
    build_manifest_payload,
    build_manual_downloads_payload,
    create_acquisition_run,
    enqueue_acquisition_run,
    mark_manual_complete,
    register_manual_upload,
)
from ..auth import require_api_key
from ..db import get_db
from ..models import AcquisitionItem, AcquisitionRun, Artifact, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    AcquisitionItemsListResponse,
    AcquisitionItemOut,
    AcquisitionManifestResponse,
    AcquisitionRunCreateRequest,
    AcquisitionRunCreateResponse,
    AcquisitionRunStatusResponse,
    ArtifactOut,
    BatchUploadMatchOut,
    BatchUploadResponse,
    ManualCompleteRequest,
    ManualDownloadItemOut,
    ManualDownloadsListResponse,
    ManualUploadRequest,
    ManualUploadResponse,
)

router = APIRouter(tags=["acquisition"])

HOT_READ_LIMIT_WINDOW_SECONDS = 10.0
HOT_READ_LIMIT_COUNT = 120
_hot_read_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return None


def _stage_status(status_value: str) -> str:
    normalized = (status_value or "").strip().lower()
    if normalized == "queued":
        return "queued"
    if normalized == "running":
        return "running"
    if normalized == "completed":
        return "completed"
    if normalized == "failed":
        return "failed"
    return "idle"


def _hot_read_client_key(request) -> str:
    ip = request.client.host if request.client else "unknown"
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        token_tail = token[-6:] if token else "none"
    else:
        token_tail = "none"
    return f"{ip}:{token_tail}"


def _guard_hot_read(request, endpoint_name: str) -> None:
    key = (_hot_read_client_key(request), endpoint_name)
    now = time.time()
    bucket = _hot_read_buckets[key]
    cutoff = now - HOT_READ_LIMIT_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    bucket.append(now)
    if len(bucket) > HOT_READ_LIMIT_COUNT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="read_rate_limited")


def _extract_doi(text: str) -> str | None:
    match = re.search(r"(10\.\d{4,9}/[-._;()/:a-z0-9]+)", text.lower())
    if not match:
        return None
    return match.group(1).rstrip(").,;")


def _title_tokens(value: str) -> set[str]:
    parts = re.split(r"[^a-z0-9]+", value.lower())
    return {part for part in parts if len(part) >= 3}


def _enqueue_acquisition_task(background_tasks: BackgroundTasks, acq_run_id: str) -> None:
    try:
        from .. import main as main_module

        enqueue_fn = getattr(main_module, "enqueue_acquisition_run", enqueue_acquisition_run)
    except Exception:
        enqueue_fn = enqueue_acquisition_run
    background_tasks.add_task(enqueue_fn, acq_run_id)


@router.post("/v1/acquisition/runs", response_model=AcquisitionRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_acq_run(
    payload: AcquisitionRunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionRunCreateResponse:
    try:
        run = create_acquisition_run(
            db,
            payload.run_id,
            retry_failed_only=payload.retry_failed_only,
            selected_source_ids=payload.selected_source_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete") from exc

    _enqueue_acquisition_task(background_tasks, run.id)
    return AcquisitionRunCreateResponse(acq_run_id=run.id, status=run.status)


@router.get("/v1/acquisition/runs/{acq_run_id}", response_model=AcquisitionRunStatusResponse)
def get_acq_run_status(
    acq_run_id: str,
    request: Request,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionRunStatusResponse:
    _guard_hot_read(request, "acquisition_run_status")
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    total = max(int(run.total_sources or 0), 1)
    completed = int((run.downloaded_total or 0) + (run.partial_total or 0) + (run.failed_total or 0) + (run.skipped_total or 0))
    completed = min(completed, total)
    percent = round((completed / total) * 100.0, 1) if total > 0 else None
    stage_status = _stage_status(run.status)
    if stage_status == "queued":
        message = "Queued to process approved documents."
    elif stage_status == "running":
        message = "Processing approved documents and retrieving files."
    elif stage_status == "failed":
        message = run.error_message or "Acquisition failed."
    else:
        message = "Acquisition completed."
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
        current_stage="acquisition",
        stage_status=stage_status,
        completed=completed,
        total=total,
        percent=percent,
        message=message,
        started_at=_iso_or_none(run.created_at),
        updated_at=_iso_or_none(run.updated_at),
    )


@router.get("/v1/acquisition/runs/{acq_run_id}/items", response_model=AcquisitionItemsListResponse)
def list_acq_items(
    acq_run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionItemsListResponse:
    _guard_hot_read(request, "acquisition_items")
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


@router.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads", response_model=ManualDownloadsListResponse)
def list_manual_downloads(
    acq_run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ManualDownloadsListResponse:
    _guard_hot_read(request, "manual_downloads")
    try:
        payload = build_manual_downloads_payload(db, acq_run_id, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
    return ManualDownloadsListResponse(
        acq_run_id=payload["acq_run_id"],
        items=[ManualDownloadItemOut(**item) for item in payload["items"]],
        total=payload["total"],
        limit=payload["limit"],
        offset=payload["offset"],
    )


@router.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads.csv")
def export_manual_downloads_csv(
    acq_run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
):
    try:
        payload = build_manual_downloads_payload(db, acq_run_id, limit=100_000, offset=0)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["title", "authors", "year", "journal", "citations", "ai_score", "status", "source_link"])
    for item in payload["items"]:
        source = db.get(Source, item["source_id"])
        writer.writerow(
            [
                (source.title if source else item["title"]) or "",
                "",
                source.year if source else "",
                "",
                "",
                str(source.relevance_score) if source and source.relevance_score is not None else "",
                item["status"],
                item["selected_url"] or item["source_url"] or "",
            ]
        )

    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="manual_downloads_{acq_run_id}.csv"',
        },
    )


@router.post("/v1/acquisition/runs/{acq_run_id}/manual-upload", response_model=ManualUploadResponse)
def manual_upload_registration(
    acq_run_id: str,
    payload: ManualUploadRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ManualUploadResponse:
    try:
        content = base64.b64decode(payload.content_base64.encode("utf-8"), validate=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_base64") from exc
    try:
        artifact = register_manual_upload(
            db,
            acq_run_id=acq_run_id,
            source_id=payload.source_id,
            filename=payload.filename,
            content_type=payload.content_type,
            content=content,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason == "acq_run_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
        if reason in {"source_not_found", "item_not_found"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=reason) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason) from exc

    return ManualUploadResponse(
        artifact_id=artifact.id,
        acq_run_id=artifact.acq_run_id,
        source_id=artifact.source_id,
        kind=artifact.kind,
        path=artifact.path,
        checksum_sha256=artifact.checksum_sha256,
        size_bytes=artifact.size_bytes,
        mime_type=artifact.mime_type,
    )


@router.post("/v1/acquisition/runs/{acq_run_id}/manual-complete")
def manual_complete_registration(
    acq_run_id: str,
    payload: ManualCompleteRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = mark_manual_complete(db, acq_run_id=acq_run_id, source_id=payload.source_id)
    except ValueError as exc:
        reason = str(exc)
        if reason == "acq_run_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item_not_found") from exc
    return {
        "acq_run_id": acq_run_id,
        "source_id": item.source_id,
        "status": item.status,
        "reason_code": item.reason_code,
    }


@router.post("/v1/acquisition/runs/{acq_run_id}/manual-upload-batch", response_model=BatchUploadResponse)
def manual_upload_batch(
    acq_run_id: str,
    files: list[UploadFile] = File(...),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> BatchUploadResponse:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    rows = db.execute(
        select(AcquisitionItem, Source)
        .join(Source, Source.id == AcquisitionItem.source_id)
        .where(AcquisitionItem.acq_run_id == acq_run_id, AcquisitionItem.status != "downloaded")
    ).all()
    candidates = [
        {
            "source_id": source.id,
            "title": source.title or "",
            "doi": (source.doi or "").lower().strip(),
            "tokens": _title_tokens(source.title or ""),
        }
        for item, source in rows
    ]
    items: list[BatchUploadMatchOut] = []
    matched = 0
    unmatched = 0
    ambiguous = 0

    for upload in files:
        filename = upload.filename or "unknown"
        content = upload.file.read()
        checksum = hashlib.sha256(content).hexdigest() if content else ""
        existing = (
            db.scalars(select(Artifact.id).where(Artifact.acq_run_id == acq_run_id, Artifact.checksum_sha256 == checksum).limit(1)).first()
            if checksum
            else None
        )
        if existing:
            items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason="duplicate_checksum"))
            unmatched += 1
            continue

        preview = content[:4096].decode("latin-1", errors="ignore").lower() if content else ""
        doi = _extract_doi(f"{filename} {preview}") or ""
        if doi:
            doi_hits = [row for row in candidates if row["doi"] and row["doi"] == doi]
            if len(doi_hits) == 1:
                target = doi_hits[0]
                try:
                    register_manual_upload(
                        db,
                        acq_run_id=acq_run_id,
                        source_id=target["source_id"],
                        filename=filename,
                        content_type=upload.content_type,
                        content=content,
                    )
                    items.append(BatchUploadMatchOut(filename=filename, status="matched", source_id=target["source_id"], score=1.0, reason="doi_exact"))
                    matched += 1
                except ValueError as exc:
                    items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
                    unmatched += 1
                continue
            if len(doi_hits) > 1:
                items.append(BatchUploadMatchOut(filename=filename, status="ambiguous", reason="multiple_doi_matches"))
                ambiguous += 1
                continue

        file_tokens = _title_tokens(Path(filename).stem)
        scored: list[tuple[float, dict]] = []
        for candidate in candidates:
            if not file_tokens or not candidate["tokens"]:
                continue
            overlap = len(file_tokens & candidate["tokens"])
            if overlap == 0:
                continue
            denom = max(len(file_tokens), len(candidate["tokens"]))
            score = overlap / denom
            if score >= 0.5:
                scored.append((score, candidate))
        scored.sort(key=lambda row: row[0], reverse=True)
        if not scored:
            items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason="no_match"))
            unmatched += 1
            continue
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.1:
            items.append(BatchUploadMatchOut(filename=filename, status="ambiguous", reason="title_match_conflict"))
            ambiguous += 1
            continue

        best_score, best = scored[0]
        try:
            register_manual_upload(
                db,
                acq_run_id=acq_run_id,
                source_id=best["source_id"],
                filename=filename,
                content_type=upload.content_type,
                content=content,
            )
            items.append(
                BatchUploadMatchOut(
                    filename=filename,
                    status="matched",
                    source_id=best["source_id"],
                    score=round(float(best_score), 3),
                    reason="title_similarity",
                )
            )
            matched += 1
        except ValueError as exc:
            items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
            unmatched += 1

    return BatchUploadResponse(acq_run_id=acq_run_id, matched=matched, unmatched=unmatched, ambiguous=ambiguous, items=items)


@router.get("/v1/acquisition/artifacts/{artifact_id}", response_model=ArtifactOut)
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


@router.get("/v1/acquisition/runs/{acq_run_id}/manifest", response_model=AcquisitionManifestResponse)
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
