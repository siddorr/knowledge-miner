from __future__ import annotations

import base64
import csv
import difflib
import hashlib
import io
import logging
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
from ..ai_filter import extract_document_identity
from ..auth import require_api_key
from ..db import get_db
from ..models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source
from ..rate_limit import require_rate_limit
from ..runtime_state import request_run_stop
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
logger = logging.getLogger("knowledge_miner")
_UPLOAD_PYPDF_MISSING_LOGGED = False

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


def _extract_upload_text(filename: str, content_type: str | None, content: bytes) -> str:
    global _UPLOAD_PYPDF_MISSING_LOGGED
    if not content:
        return ""
    is_pdf = (content_type or "").lower() == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            if not _UPLOAD_PYPDF_MISSING_LOGGED:
                logger.warning("manual_upload_pdf_text_extraction_unavailable parser=pypdf reason=module_not_installed")
                _UPLOAD_PYPDF_MISSING_LOGGED = True
            return ""
        try:
            reader = PdfReader(io.BytesIO(content))
            page_texts: list[str] = []
            for page in reader.pages[:2]:
                try:
                    extracted = page.extract_text() or ""
                except Exception:
                    extracted = ""
                cleaned = re.sub(r"\s+", " ", extracted).strip()
                if cleaned:
                    page_texts.append(cleaned)
            return "\n".join(page_texts)[:4000]
        except Exception:
            return ""
    return content[:4000].decode("latin-1", errors="ignore")


def _pdf_text_parser_available() -> bool:
    try:
        from pypdf import PdfReader  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _candidate_title_score(file_tokens: set[str], text_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not candidate_tokens:
        return 0.0
    file_overlap = len(file_tokens & candidate_tokens) / max(len(candidate_tokens), 1) if file_tokens else 0.0
    text_overlap = len(text_tokens & candidate_tokens) / max(len(candidate_tokens), 1) if text_tokens else 0.0
    return max(file_overlap, text_overlap)


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _extract_probable_pdf_title(text: str) -> str | None:
    if not text:
        return None
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    ignore_prefixes = {
        "abstract",
        "keywords",
        "received",
        "accepted",
        "published",
        "doi",
        "http",
        "www.",
    }
    candidates: list[tuple[float, str]] = []
    for idx, line in enumerate(lines[:20]):
        lowered = line.lower()
        if any(lowered.startswith(prefix) for prefix in ignore_prefixes):
            continue
        if "@" in line:
            continue
        if len(line) < 20 or len(line) > 220:
            continue
        alpha_ratio = sum(char.isalpha() for char in line) / max(len(line), 1)
        if alpha_ratio < 0.55:
            continue
        score = alpha_ratio + (0.15 if idx < 8 else 0.0)
        if not lowered.endswith("."):
            score += 0.05
        if line.count(",") <= 2 and line.count(";") == 0:
            score += 0.05
        candidates.append((score, line))
        if idx + 1 < len(lines[:20]):
            joined = f"{line} {lines[idx + 1]}".strip()
            if 30 <= len(joined) <= 260:
                joined_alpha_ratio = sum(char.isalpha() for char in joined) / max(len(joined), 1)
                if joined_alpha_ratio >= 0.6:
                    candidates.append((score + 0.03, joined))
    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[0][1]


def _title_similarity(left: str, right: str) -> float:
    left_norm = _normalize_match_text(left)
    right_norm = _normalize_match_text(right)
    if not left_norm or not right_norm:
        return 0.0
    token_overlap = len(set(left_norm.split()) & set(right_norm.split())) / max(len(set(right_norm.split())), 1)
    seq_ratio = difflib.SequenceMatcher(a=left_norm, b=right_norm).ratio()
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return max(seq_ratio, 0.92)
    return max(token_overlap, seq_ratio)


def _shorten_log_value(value: str | None, limit: int = 160) -> str:
    if not value:
        return "-"
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


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
            internal_repository_base_url=payload.internal_repository_base_url,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason == "run_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
        if reason == "invalid_internal_repository_base_url":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_internal_repository_base_url") from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete") from exc

    _enqueue_acquisition_task(background_tasks, run.id)
    return AcquisitionRunCreateResponse(acq_run_id=run.id, status=run.status)


@router.post("/v1/acquisition/runs/manual-context", response_model=AcquisitionRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_manual_upload_context(
    payload: AcquisitionRunCreateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionRunCreateResponse:
    try:
        run = create_acquisition_run(
            db,
            payload.run_id,
            retry_failed_only=False,
            selected_source_ids=payload.selected_source_ids,
            internal_repository_base_url=payload.internal_repository_base_url,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason == "run_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
        if reason == "invalid_internal_repository_base_url":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_internal_repository_base_url") from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete") from exc

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


@router.post("/v1/acquisition/runs/{acq_run_id}/stop")
def stop_acq_run(
    acq_run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    if run.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_running")
    if run.status == "queued":
        run.status = "failed"
        run.error_message = "stopped_by_user"
        db.commit()
        return {"acq_run_id": run.id, "status": run.status, "message": "Acquisition run stopped."}

    request_run_stop(base_dir=settings.runtime_state_dir, phase="acquisition", run_id=acq_run_id)
    return {"acq_run_id": run.id, "status": run.status, "message": "Stop requested."}


@router.get("/v1/acquisition/runs/{acq_run_id}/items", response_model=AcquisitionItemsListResponse)
def list_acq_items(
    acq_run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=5000),
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


@router.get("/v1/sessions/{session_id}/acquisition-items/latest", response_model=AcquisitionItemsListResponse)
def list_session_latest_acq_items(
    session_id: str,
    request: Request,
    limit: int = Query(default=5000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> AcquisitionItemsListResponse:
    _guard_hot_read(request, "session_acquisition_items_latest")
    rows = db.execute(
        select(AcquisitionItem, AcquisitionRun, Source, Run)
        .join(Source, Source.id == AcquisitionItem.source_id)
        .join(Run, Run.id == Source.run_id)
        .join(AcquisitionRun, AcquisitionRun.id == AcquisitionItem.acq_run_id)
        .where(Run.session_id == session_id)
        .order_by(
            AcquisitionItem.source_id.asc(),
            AcquisitionItem.updated_at.desc(),
            AcquisitionItem.id.desc(),
            AcquisitionRun.created_at.desc(),
            AcquisitionRun.id.desc(),
        )
    ).all()
    latest_by_source: dict[str, AcquisitionItem] = {}
    for item, _acq_run, _source, _run in rows:
        latest_by_source.setdefault(item.source_id, item)
    items = list(latest_by_source.values())
    page = items[offset : offset + limit]
    return AcquisitionItemsListResponse(
        items=[
            AcquisitionItemOut(
                item_id=item.id,
                source_id=item.source_id,
                status=item.status,
                attempt_count=item.attempt_count,
                selected_url=item.selected_url,
                last_error=item.last_error,
            )
            for item in page
        ],
        total=len(items),
        limit=limit,
        offset=offset,
    )


@router.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads", response_model=ManualDownloadsListResponse)
def list_manual_downloads(
    acq_run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=5000),
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
                ", ".join(source.authors or []) if source else "",
                source.year if source else "",
                (source.journal or "") if source else "",
                source.citation_count if source and source.citation_count is not None else "",
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
        log_reason = "unknown"
        extracted_doi = ""
        probable_title = None
        ai_identity = None
        checksum = hashlib.sha256(content).hexdigest() if content else ""
        existing = (
            db.scalars(select(Artifact.id).where(Artifact.acq_run_id == acq_run_id, Artifact.checksum_sha256 == checksum).limit(1)).first()
            if checksum
            else None
        )
        if existing:
            log_reason = "duplicate_checksum"
            items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason="duplicate_checksum"))
            unmatched += 1
            logger.info(
                "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                acq_run_id,
                filename,
                "unmatched",
                log_reason,
                "-",
                "-",
                "-",
                "-",
                "-",
            )
            continue

        extracted_text = _extract_upload_text(filename, upload.content_type, content)
        preview = extracted_text.lower() if extracted_text else content[:4096].decode("latin-1", errors="ignore").lower()
        extracted_doi = _extract_doi(f"{filename} {preview}") or ""
        parser_unavailable = (
            ((upload.content_type or "").lower() == "application/pdf" or filename.lower().endswith(".pdf"))
            and not _pdf_text_parser_available()
        )
        if extracted_doi:
            doi_hits = [row for row in candidates if row["doi"] and row["doi"] == extracted_doi]
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
                    log_reason = "doi_exact"
                    items.append(BatchUploadMatchOut(filename=filename, status="matched", source_id=target["source_id"], score=1.0, reason=log_reason))
                    matched += 1
                except ValueError as exc:
                    log_reason = str(exc)
                    items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
                    unmatched += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    items[-1].status,
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    "-",
                    "-",
                    "-",
                    "-",
                )
                continue
            if len(doi_hits) > 1:
                log_reason = "multiple_doi_matches"
                items.append(BatchUploadMatchOut(filename=filename, status="ambiguous", reason=log_reason))
                ambiguous += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    "ambiguous",
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    "-",
                    "-",
                    "-",
                    "-",
                )
                continue

        probable_title = _extract_probable_pdf_title(extracted_text)
        if probable_title:
            title_scored = [
                (_title_similarity(probable_title, candidate["title"]), candidate)
                for candidate in candidates
                if candidate["title"]
            ]
            title_scored = [row for row in title_scored if row[0] >= 0.82]
            title_scored.sort(key=lambda row: row[0], reverse=True)
            if len(title_scored) == 1 or (title_scored and (len(title_scored) == 1 or title_scored[0][0] - title_scored[1][0] >= 0.08)):
                best_score, best = title_scored[0]
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
                            reason="pdf_title_similarity",
                        )
                    )
                    log_reason = "pdf_title_similarity"
                    matched += 1
                except ValueError as exc:
                    log_reason = str(exc)
                    items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
                    unmatched += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    items[-1].status,
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    _shorten_log_value(probable_title),
                    "-",
                    "-",
                    "-",
                )
                continue
            if len(title_scored) > 1 and abs(title_scored[0][0] - title_scored[1][0]) < 0.08:
                log_reason = "pdf_title_conflict"
                items.append(BatchUploadMatchOut(filename=filename, status="ambiguous", reason=log_reason))
                ambiguous += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    "ambiguous",
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    _shorten_log_value(probable_title),
                    "-",
                    "-",
                    "-",
                )
                continue

        if extracted_text:
            try:
                ai_identity = extract_document_identity(filename=filename, first_page_text=extracted_text)
            except Exception:
                ai_identity = None
        if ai_identity and ai_identity.doi:
            doi_hits = [row for row in candidates if row["doi"] and row["doi"] == ai_identity.doi]
            if len(doi_hits) == 1 and ai_identity.confidence >= 0.7:
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
                    items.append(
                        BatchUploadMatchOut(
                            filename=filename,
                            status="matched",
                            source_id=target["source_id"],
                            score=round(float(ai_identity.confidence), 3),
                            reason="ai_doi_exact",
                        )
                    )
                    log_reason = "ai_doi_exact"
                    matched += 1
                except ValueError as exc:
                    log_reason = str(exc)
                    items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
                    unmatched += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    items[-1].status,
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    _shorten_log_value(probable_title),
                    _shorten_log_value(ai_identity.title),
                    _shorten_log_value(ai_identity.doi),
                    ai_identity.confidence,
                )
                continue
        if ai_identity and ai_identity.title and ai_identity.confidence >= 0.7:
            ai_title_scored = [
                (_title_similarity(ai_identity.title, candidate["title"]), candidate)
                for candidate in candidates
                if candidate["title"]
            ]
            ai_title_scored = [row for row in ai_title_scored if row[0] >= 0.8]
            ai_title_scored.sort(key=lambda row: row[0], reverse=True)
            if len(ai_title_scored) == 1 or (ai_title_scored and (len(ai_title_scored) == 1 or ai_title_scored[0][0] - ai_title_scored[1][0] >= 0.08)):
                best_score, best = ai_title_scored[0]
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
                            score=round(float(max(best_score, ai_identity.confidence)), 3),
                            reason="ai_title_similarity",
                        )
                    )
                    log_reason = "ai_title_similarity"
                    matched += 1
                except ValueError as exc:
                    log_reason = str(exc)
                    items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
                    unmatched += 1
                logger.info(
                    "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                    acq_run_id,
                    filename,
                    items[-1].status,
                    log_reason,
                    _shorten_log_value(extracted_doi),
                    _shorten_log_value(probable_title),
                    _shorten_log_value(ai_identity.title),
                    _shorten_log_value(ai_identity.doi),
                    ai_identity.confidence,
                )
                continue

        file_tokens = _title_tokens(Path(filename).stem)
        text_tokens = _title_tokens(extracted_text)
        scored: list[tuple[float, dict]] = []
        for candidate in candidates:
            if not candidate["tokens"]:
                continue
            score = _candidate_title_score(file_tokens, text_tokens, candidate["tokens"])
            if score == 0:
                continue
            if score >= 0.6:
                scored.append((score, candidate))
        scored.sort(key=lambda row: row[0], reverse=True)
        if not scored:
            log_reason = "pdf_parser_unavailable" if parser_unavailable else "no_match"
            items.append(
                BatchUploadMatchOut(
                    filename=filename,
                    status="unmatched",
                    reason=log_reason,
                )
            )
            unmatched += 1
            logger.info(
                "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                acq_run_id,
                filename,
                "unmatched",
                log_reason,
                _shorten_log_value(extracted_doi),
                _shorten_log_value(probable_title),
                _shorten_log_value(ai_identity.title if ai_identity else None),
                _shorten_log_value(ai_identity.doi if ai_identity else None),
                ai_identity.confidence if ai_identity else "-",
            )
            continue
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.1:
            log_reason = "title_match_conflict"
            items.append(BatchUploadMatchOut(filename=filename, status="ambiguous", reason=log_reason))
            ambiguous += 1
            logger.info(
                "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
                acq_run_id,
                filename,
                "ambiguous",
                log_reason,
                _shorten_log_value(extracted_doi),
                _shorten_log_value(probable_title),
                _shorten_log_value(ai_identity.title if ai_identity else None),
                _shorten_log_value(ai_identity.doi if ai_identity else None),
                ai_identity.confidence if ai_identity else "-",
            )
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
            log_reason = "title_similarity"
            matched += 1
        except ValueError as exc:
            log_reason = str(exc)
            items.append(BatchUploadMatchOut(filename=filename, status="unmatched", reason=str(exc)))
            unmatched += 1
        logger.info(
            "manual_upload_batch_item acq_run_id=%s filename=%s status=%s reason=%s extracted_doi=%s probable_title=%s ai_title=%s ai_doi=%s ai_confidence=%s",
            acq_run_id,
            filename,
            items[-1].status,
            log_reason,
            _shorten_log_value(extracted_doi),
            _shorten_log_value(probable_title),
            _shorten_log_value(ai_identity.title if ai_identity else None),
            _shorten_log_value(ai_identity.doi if ai_identity else None),
            ai_identity.confidence if ai_identity else "-",
        )

    logger.info(
        "manual_upload_batch_summary acq_run_id=%s files=%s matched=%s unmatched=%s ambiguous=%s",
        acq_run_id,
        len(files),
        matched,
        unmatched,
        ambiguous,
    )
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
