from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import NamedTuple
from urllib.parse import urlparse
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source
from .observability import AcquisitionObservability


def create_acquisition_run(db: Session, discovery_run_id: str, *, retry_failed_only: bool) -> AcquisitionRun:
    run = db.get(Run, discovery_run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != "completed":
        raise RuntimeError("run_not_complete")

    accepted_sources = db.scalars(
        select(Source).where(Source.run_id == discovery_run_id, Source.accepted.is_(True)).order_by(Source.id.asc())
    ).all()
    selected_sources = accepted_sources
    if retry_failed_only:
        prev_run = db.scalars(
            select(AcquisitionRun)
            .where(AcquisitionRun.discovery_run_id == discovery_run_id)
            .order_by(AcquisitionRun.created_at.desc(), AcquisitionRun.id.desc())
        ).first()
        if prev_run is not None:
            failed_source_ids = set(
                db.scalars(
                    select(AcquisitionItem.source_id).where(
                        AcquisitionItem.acq_run_id == prev_run.id,
                        AcquisitionItem.status == "failed",
                    )
                ).all()
            )
            selected_sources = [source for source in accepted_sources if source.id in failed_source_ids]
        else:
            selected_sources = []
    acq_run = AcquisitionRun(
        id=f"acq_{uuid.uuid4().hex[:12]}",
        discovery_run_id=discovery_run_id,
        retry_failed_only=retry_failed_only,
        status="queued",
        total_sources=len(selected_sources),
        downloaded_total=0,
        partial_total=0,
        failed_total=0,
        skipped_total=0,
    )
    db.add(acq_run)
    db.flush()

    for source in selected_sources:
        db.add(
            AcquisitionItem(
                id=f"acq_item_{uuid.uuid4().hex[:12]}",
                acq_run_id=acq_run.id,
                source_id=source.id,
                status="queued",
                attempt_count=0,
                selected_url=source.url,
                last_error=None,
            )
        )

    db.commit()
    db.refresh(acq_run)
    return acq_run


def enqueue_acquisition_run(acq_run_id: str) -> None:
    worker = threading.Thread(target=execute_acquisition_run_by_id, args=(acq_run_id,), daemon=True)
    worker.start()


def execute_acquisition_run_by_id(acq_run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(AcquisitionRun, acq_run_id)
        if run is None:
            return
        execute_acquisition_run(db, run)


def execute_acquisition_run(db: Session, run: AcquisitionRun) -> None:
    observability = AcquisitionObservability()
    try:
        run.status = "running"
        run.updated_at = datetime.now(UTC)
        db.commit()

        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == run.id)).all()
        downloaded_total = 0
        partial_total = 0
        failed_total = 0
        skipped_total = 0
        for item in items:
            if run.retry_failed_only and item.status in {"downloaded", "partial", "skipped"}:
                skipped_total += 1
                observability.inc("skipped")
                continue

            source = db.get(Source, item.source_id)
            if source is None:
                item.status = "failed"
                item.last_error = "source_not_found"
                failed_total += 1
                observability.inc("failed")
                continue

            started = time.perf_counter()
            outcome = _acquire_source_content(source)
            latency_ms = (time.perf_counter() - started) * 1000.0
            item.attempt_count += outcome.attempts
            item.selected_url = outcome.url
            item.last_error = outcome.error
            observability.inc("attempted")
            if outcome.attempts > 1:
                observability.inc("retries", outcome.attempts - 1)
            domain = _domain_from_url(outcome.url or source.url)

            if outcome.kind == "pdf":
                artifact = _persist_artifact(db, run.id, item, source.id, kind="pdf", mime_type=outcome.mime_type, content=outcome.content)
                item.status = "downloaded"
                item.last_error = None
                downloaded_total += 1
                observability.inc("downloaded")
                db.add(artifact)
                observability.record_download(
                    acq_run_id=run.id,
                    source_id=source.id,
                    domain=domain,
                    latency_ms=latency_ms,
                    status="downloaded",
                )
            elif outcome.kind == "html":
                artifact = _persist_artifact(
                    db,
                    run.id,
                    item,
                    source.id,
                    kind="html",
                    mime_type=outcome.mime_type,
                    content=outcome.content,
                )
                item.status = "partial"
                partial_total += 1
                observability.inc("partial")
                db.add(artifact)
                observability.record_download(
                    acq_run_id=run.id,
                    source_id=source.id,
                    domain=domain,
                    latency_ms=latency_ms,
                    status="partial",
                    error=outcome.error,
                )
            elif outcome.error == "no_candidate_urls":
                item.status = "skipped"
                skipped_total += 1
                observability.inc("skipped")
                observability.record_download(
                    acq_run_id=run.id,
                    source_id=source.id,
                    domain=domain,
                    latency_ms=latency_ms,
                    status="skipped",
                    error=outcome.error,
                )
            else:
                item.status = "failed"
                failed_total += 1
                observability.inc("failed")
                observability.inc("api_errors")
                observability.record_download(
                    acq_run_id=run.id,
                    source_id=source.id,
                    domain=domain,
                    latency_ms=latency_ms,
                    status="failed",
                    error=outcome.error,
                )
            item.updated_at = datetime.now(UTC)

        run.downloaded_total = downloaded_total
        run.partial_total = partial_total
        run.failed_total = failed_total
        run.skipped_total = skipped_total
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
        _write_manifest_file(db, run.id)
        observability.emit_summary(acq_run_id=run.id, status=run.status)
    except Exception as exc:  # pragma: no cover
        db.rollback()
        run.status = "failed"
        run.error_message = str(exc)
        run.updated_at = datetime.now(UTC)
        db.commit()
        observability.inc("api_errors")
        observability.emit_summary(acq_run_id=run.id, status=run.status)
        raise


class AcquisitionOutcome(NamedTuple):
    kind: str | None
    mime_type: str | None
    content: bytes | None
    url: str | None
    attempts: int
    error: str | None


def _acquire_source_content(source: Source) -> AcquisitionOutcome:
    candidate_urls = _resolve_candidate_urls(source)
    if not candidate_urls:
        return AcquisitionOutcome(
            kind=None,
            mime_type=None,
            content=None,
            url=None,
            attempts=0,
            error="no_candidate_urls",
        )

    max_bytes = int(getattr(settings, "acquisition_max_bytes", 25_000_000))
    timeout_seconds = float(getattr(settings, "acquisition_timeout_seconds", 20.0))
    pdf_first = sorted(candidate_urls, key=lambda url: (0 if _looks_pdf_url(url) else 1, url))

    attempts = 0
    html_fallback: AcquisitionOutcome | None = None
    last_error = "download_failed"
    for url in pdf_first:
        result, call_attempts = _download_with_retries(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        attempts += call_attempts
        if result.kind == "pdf":
            return AcquisitionOutcome(
                kind="pdf",
                mime_type=result.mime_type,
                content=result.content,
                url=result.url,
                attempts=attempts,
                error=None,
            )
        if result.kind == "html" and html_fallback is None:
            html_fallback = AcquisitionOutcome(
                kind="html",
                mime_type=result.mime_type,
                content=result.content,
                url=result.url,
                attempts=attempts,
                error=None,
            )
        if result.error:
            last_error = result.error

    if html_fallback is not None:
        return AcquisitionOutcome(
            kind="html",
            mime_type=html_fallback.mime_type,
            content=html_fallback.content,
            url=html_fallback.url,
            attempts=attempts,
            error="pdf_unavailable_html_fallback",
        )
    return AcquisitionOutcome(kind=None, mime_type=None, content=None, url=None, attempts=attempts, error=last_error)


def _download_with_retries(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    delays: tuple[float, ...] = (1.0, 2.0, 4.0),
    sleep=time.sleep,
) -> tuple[DownloadResult, int]:
    attempts = 0
    last = DownloadResult(kind=None, mime_type=None, content=None, url=url, error="download_failed", retryable=False)
    for index in range(max(1, len(delays))):
        attempts += 1
        result = _download_url(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        last = result
        if result.kind is not None:
            return result, attempts
        if not result.retryable:
            return result, attempts
        if index < len(delays) - 1:
            sleep(delays[index])
    return last, attempts


def _resolve_candidate_urls(source: Source) -> list[str]:
    candidates: list[str] = []
    if source.url:
        candidates.append(source.url.strip())
    if source.doi:
        doi = source.doi.strip()
        if doi:
            candidates.append(f"https://doi.org/{doi}")
    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            deduped.append(url)
            seen.add(url)
    return deduped


def _looks_pdf_url(url: str) -> bool:
    normalized = url.lower()
    return normalized.endswith(".pdf") or "/pdf" in normalized


def _domain_from_url(url: str | None) -> str:
    if not url:
        return "unknown"
    return (urlparse(url).netloc or "unknown").lower()


def _persist_artifact(
    db: Session,
    acq_run_id: str,
    item: AcquisitionItem,
    source_id: str,
    *,
    kind: str,
    mime_type: str | None,
    content: bytes | None,
) -> Artifact:
    payload = content or b""
    checksum = hashlib.sha256(payload).hexdigest()
    rel_path = Path("acquisition") / acq_run_id / source_id / f"source.{kind}"
    abs_path = Path(settings.artifacts_dir) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)

    return Artifact(
        id=f"artifact_{uuid.uuid4().hex[:12]}",
        acq_run_id=acq_run_id,
        source_id=source_id,
        item_id=item.id,
        kind=kind,
        path=str(rel_path),
        checksum_sha256=checksum,
        size_bytes=len(payload),
        mime_type=mime_type,
    )


class DownloadResult(NamedTuple):
    kind: str | None
    mime_type: str | None
    content: bytes | None
    url: str
    error: str | None
    retryable: bool


def _download_url(url: str, *, timeout_seconds: float, max_bytes: int) -> DownloadResult:
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.RequestError:
        return DownloadResult(kind=None, mime_type=None, content=None, url=url, error="network_error", retryable=True)

    if response.status_code == 429 or 500 <= response.status_code <= 599:
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=str(response.url),
            error=f"http_{response.status_code}",
            retryable=True,
        )
    if response.status_code >= 400:
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=str(response.url),
            error=f"http_{response.status_code}",
            retryable=False,
        )

    content = response.content
    if len(content) > max_bytes:
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=str(response.url),
            error="file_too_large",
            retryable=False,
        )

    raw_mime = response.headers.get("Content-Type", "")
    mime = raw_mime.split(";")[0].strip().lower() if raw_mime else None
    if mime == "application/pdf":
        return DownloadResult(kind="pdf", mime_type=mime, content=content, url=str(response.url), error=None, retryable=False)
    if mime in {"text/html", "application/xhtml+xml"}:
        return DownloadResult(kind="html", mime_type=mime, content=content, url=str(response.url), error=None, retryable=False)

    # Fallback sniffing when providers omit proper content-type.
    if content.startswith(b"%PDF"):
        return DownloadResult(
            kind="pdf",
            mime_type="application/pdf",
            content=content,
            url=str(response.url),
            error=None,
            retryable=False,
        )
    if content[:1024].lower().find(b"<html") >= 0:
        return DownloadResult(kind="html", mime_type="text/html", content=content, url=str(response.url), error=None, retryable=False)

    return DownloadResult(
        kind=None,
        mime_type=mime,
        content=None,
        url=str(response.url),
        error="unsupported_content_type",
        retryable=False,
    )


def build_manifest_payload(db: Session, acq_run_id: str) -> dict:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise ValueError("acq_run_not_found")

    items = db.scalars(
        select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id).order_by(AcquisitionItem.source_id.asc())
    ).all()
    artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == acq_run_id).order_by(Artifact.id.asc())).all()

    return {
        "acq_run_id": run.id,
        "discovery_run_id": run.discovery_run_id,
        "status": run.status,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "total_sources": run.total_sources,
            "downloaded_total": run.downloaded_total,
            "partial_total": run.partial_total,
            "failed_total": run.failed_total,
            "skipped_total": run.skipped_total,
        },
        "items": [
            {
                "item_id": i.id,
                "source_id": i.source_id,
                "status": i.status,
                "attempt_count": i.attempt_count,
                "selected_url": i.selected_url,
                "last_error": i.last_error,
            }
            for i in items
        ],
        "artifacts": [
            {
                "artifact_id": a.id,
                "source_id": a.source_id,
                "item_id": a.item_id,
                "kind": a.kind,
                "path": a.path,
                "checksum_sha256": a.checksum_sha256,
                "size_bytes": a.size_bytes,
                "mime_type": a.mime_type,
            }
            for a in artifacts
        ],
    }


def _manifest_file_path(acq_run_id: str) -> Path:
    return Path(settings.artifacts_dir) / "acquisition" / acq_run_id / "manifest.json"


def _write_manifest_file(db: Session, acq_run_id: str) -> Path:
    payload = build_manifest_payload(db, acq_run_id)
    path = _manifest_file_path(acq_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def build_manual_downloads_payload(db: Session, acq_run_id: str, *, limit: int, offset: int) -> dict:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise ValueError("acq_run_not_found")

    rows = db.execute(
        select(AcquisitionItem, Source)
        .join(Source, Source.id == AcquisitionItem.source_id)
        .where(
            AcquisitionItem.acq_run_id == acq_run_id,
            AcquisitionItem.status.in_(("failed", "partial", "skipped")),
        )
        .order_by(AcquisitionItem.source_id.asc())
    ).all()
    page = rows[offset : offset + limit]

    return {
        "acq_run_id": run.id,
        "items": [
            {
                "item_id": item.id,
                "source_id": item.source_id,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "last_error": item.last_error,
                "title": source.title,
                "doi": source.doi,
                "source_url": source.url,
                "selected_url": item.selected_url,
                "manual_url_candidates": _manual_url_candidates(source=source, item=item),
            }
            for item, source in page
        ],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


def register_manual_upload(
    db: Session,
    *,
    acq_run_id: str,
    source_id: str,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> Artifact:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise ValueError("acq_run_not_found")
    source = db.get(Source, source_id)
    if source is None:
        raise ValueError("source_not_found")
    item = db.scalars(
        select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id, AcquisitionItem.source_id == source_id)
    ).first()
    if item is None:
        raise ValueError("item_not_found")
    if not content:
        raise ValueError("empty_content")

    max_bytes = int(getattr(settings, "acquisition_max_bytes", 25_000_000))
    if len(content) > max_bytes:
        raise ValueError("file_too_large")

    kind, mime_type = _detect_manual_upload_kind(filename=filename, content_type=content_type, content=content)
    if kind is None or mime_type is None:
        raise ValueError("unsupported_content_type")

    checksum = hashlib.sha256(content).hexdigest()
    rel_path = Path("acquisition") / acq_run_id / source_id / f"manual_upload.{kind}"
    abs_path = Path(settings.artifacts_dir) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    artifact = Artifact(
        id=f"artifact_{uuid.uuid4().hex[:12]}",
        acq_run_id=acq_run_id,
        source_id=source_id,
        item_id=item.id,
        kind=kind,
        path=str(rel_path),
        checksum_sha256=checksum,
        size_bytes=len(content),
        mime_type=mime_type,
    )
    db.add(artifact)
    item.status = "downloaded"
    item.last_error = None
    item.updated_at = datetime.now(UTC)
    _recompute_acquisition_totals(db, run)
    db.commit()
    db.refresh(artifact)
    return artifact


def _manual_url_candidates(*, source: Source, item: AcquisitionItem) -> list[str]:
    urls = [source.url, f"https://doi.org/{source.doi}" if source.doi else None, item.selected_url]
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        if not raw:
            continue
        normalized = _normalize_url(raw)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(raw)
    return deduped


def _normalize_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}{query}"


def _detect_manual_upload_kind(*, filename: str, content_type: str | None, content: bytes) -> tuple[str | None, str | None]:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return "pdf", "application/pdf"
    if lowered.endswith(".html") or lowered.endswith(".htm"):
        return "html", "text/html"

    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf", "application/pdf"
    if ct in {"text/html", "application/xhtml+xml"}:
        return "html", "text/html"

    if content.startswith(b"%PDF"):
        return "pdf", "application/pdf"
    if content[:1024].lower().find(b"<html") >= 0:
        return "html", "text/html"
    return None, None


def _recompute_acquisition_totals(db: Session, run: AcquisitionRun) -> None:
    db.flush()
    statuses = db.scalars(select(AcquisitionItem.status).where(AcquisitionItem.acq_run_id == run.id)).all()
    run.downloaded_total = sum(1 for s in statuses if s == "downloaded")
    run.partial_total = sum(1 for s in statuses if s == "partial")
    run.failed_total = sum(1 for s in statuses if s == "failed")
    run.skipped_total = sum(1 for s in statuses if s == "skipped")
    run.updated_at = datetime.now(UTC)
