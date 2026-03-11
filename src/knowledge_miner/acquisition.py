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
from .runtime_state import acquire_run_lock, is_primary_instance, release_run_lock


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
                selected_url_source=None,
                resolution_attempts=[],
                reason_code=None,
                last_error=None,
            )
        )

    db.commit()
    db.refresh(acq_run)
    return acq_run


def enqueue_acquisition_run(acq_run_id: str) -> None:
    if not is_primary_instance():
        return
    run_lock = acquire_run_lock(base_dir=settings.runtime_state_dir, phase="acquisition", run_id=acq_run_id)
    if run_lock is None:
        return
    worker = threading.Thread(target=_execute_acquisition_run_with_lock, args=(acq_run_id, run_lock), daemon=True)
    worker.start()


def _execute_acquisition_run_with_lock(acq_run_id: str, run_lock: Path) -> None:
    try:
        execute_acquisition_run_by_id(acq_run_id)
    finally:
        release_run_lock(run_lock)


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
                item.reason_code = "source_error"
                failed_total += 1
                observability.inc("failed")
                continue

            started = time.perf_counter()
            outcome = _acquire_source_content(source)
            latency_ms = (time.perf_counter() - started) * 1000.0
            item.attempt_count += outcome.attempts
            item.selected_url = outcome.url
            item.selected_url_source = outcome.selected_url_source
            item.resolution_attempts = outcome.resolution_attempts
            item.reason_code = outcome.reason_code
            item.last_error = outcome.error
            observability.inc("attempted")
            if outcome.attempts > 1:
                observability.inc("retries", outcome.attempts - 1)
            domain = _domain_from_url(outcome.url or source.url)
            if outcome.selected_url_source == "openalex":
                observability.inc("resolved_via_openalex")
            elif outcome.selected_url_source == "unpaywall":
                observability.inc("resolved_via_unpaywall")
            elif outcome.selected_url_source in {"pmc", "arxiv"}:
                observability.inc("resolved_via_repository")

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
                observability.inc("manual_recovery_required")
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
                observability.inc("manual_recovery_required")
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
                observability.inc("manual_recovery_required")
                if outcome.reason_code == "paywalled":
                    observability.inc("paywalled")
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
        _write_coverage_report_file(run.id, observability.snapshot())
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
    selected_url_source: str | None
    resolution_attempts: list[dict]
    attempts: int
    error: str | None
    reason_code: str | None


def _acquire_source_content(source: Source) -> AcquisitionOutcome:
    resolution_attempts = _resolve_candidate_chain(source)
    if not resolution_attempts:
        return AcquisitionOutcome(
            kind=None,
            mime_type=None,
            content=None,
            url=None,
            selected_url_source=None,
            resolution_attempts=[],
            attempts=0,
            error="no_candidate_urls",
            reason_code="no_oa_found",
        )

    max_bytes = int(getattr(settings, "acquisition_max_bytes", 25_000_000))
    timeout_seconds = float(getattr(settings, "acquisition_timeout_seconds", 20.0))
    pdf_first = sorted(
        resolution_attempts,
        key=lambda candidate: (
            0 if _looks_pdf_url(str(candidate.get("candidate_url") or "")) else 1,
            int(candidate.get("candidate_rank", 999999)),
        ),
    )

    attempts = 0
    html_fallback: AcquisitionOutcome | None = None
    last_error = "download_failed"
    for candidate in pdf_first:
        url = str(candidate.get("candidate_url") or "")
        source_name = str(candidate.get("candidate_source") or "publisher")
        if not url:
            continue
        result, call_attempts = _download_with_retries(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        attempts += call_attempts
        if result.kind == "pdf":
            return AcquisitionOutcome(
                kind="pdf",
                mime_type=result.mime_type,
                content=result.content,
                url=result.url,
                selected_url_source=source_name,
                resolution_attempts=resolution_attempts,
                attempts=attempts,
                error=None,
                reason_code=None,
            )
        if result.kind == "html" and html_fallback is None:
            html_fallback = AcquisitionOutcome(
                kind="html",
                mime_type=result.mime_type,
                content=result.content,
                url=result.url,
                selected_url_source=source_name,
                resolution_attempts=resolution_attempts,
                attempts=attempts,
                error=None,
                reason_code=None,
            )
        if result.error:
            last_error = result.error

    if html_fallback is not None:
        return AcquisitionOutcome(
            kind="html",
            mime_type=html_fallback.mime_type,
            content=html_fallback.content,
            url=html_fallback.url,
            selected_url_source=html_fallback.selected_url_source,
            resolution_attempts=resolution_attempts,
            attempts=attempts,
            error="pdf_unavailable_html_fallback",
            reason_code="source_error",
        )
    return AcquisitionOutcome(
        kind=None,
        mime_type=None,
        content=None,
        url=None,
        selected_url_source=None,
        resolution_attempts=resolution_attempts,
        attempts=attempts,
        error=last_error,
        reason_code=_reason_code_from_error(last_error),
    )


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


def _resolve_candidate_chain(source: Source) -> list[dict]:
    candidates: list[tuple[str, str]] = []
    if source.doi:
        doi = source.doi.strip()
        if doi:
            candidates.append((f"https://doi.org/{doi}", "doi"))
    openalex_url = _lookup_openalex_oa_url(source)
    if openalex_url:
        candidates.append((openalex_url, "openalex"))
    unpaywall_url = _lookup_unpaywall_oa_url(source)
    if unpaywall_url:
        candidates.append((unpaywall_url, "unpaywall"))
    for repository_url, repository_source in _lookup_repository_urls(source):
        candidates.append((repository_url, repository_source))
    if source.url:
        candidates.append((source.url.strip(), "publisher"))

    deduped: list[dict] = []
    seen: set[str] = set()
    rank = 0
    for raw_url, source_name in candidates:
        normalized = _normalize_url(raw_url)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        rank += 1
        deduped.append(
            {
                "candidate_url": raw_url,
                "candidate_source": source_name,
                "candidate_rank": rank,
            }
        )
    return deduped


def _resolve_candidate_urls(source: Source) -> list[str]:
    return [entry["candidate_url"] for entry in _resolve_candidate_chain(source)]


def _lookup_openalex_oa_url(source: Source) -> str | None:
    if source.source == "openalex" and source.url:
        return source.url
    return None


def _lookup_unpaywall_oa_url(source: Source) -> str | None:
    return None


def _lookup_repository_urls(source: Source) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    if not source.url:
        return urls
    parsed = urlparse(source.url)
    host = (parsed.netloc or "").lower()
    if "arxiv.org" in host:
        path = parsed.path.strip("/")
        if path.startswith("abs/"):
            arxiv_id = path.removeprefix("abs/")
            if arxiv_id:
                urls.append((f"https://arxiv.org/pdf/{arxiv_id}.pdf", "arxiv"))
        else:
            urls.append((source.url, "arxiv"))
    if "ncbi.nlm.nih.gov" in host and "/pmc/" in parsed.path.lower():
        urls.append((source.url, "pmc"))
    return urls


def _reason_code_from_error(error: str | None) -> str:
    if error in {"http_401", "http_403"}:
        return "paywalled"
    if error == "http_429":
        return "rate_limited"
    if error in {"http_451", "robots_blocked"}:
        return "robots_blocked"
    if error in {"no_candidate_urls", "download_failed"}:
        return "no_oa_found"
    return "source_error"


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

    if 500 <= response.status_code <= 599:
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=str(response.url),
            error=f"http_{response.status_code}",
            retryable=True,
        )
    if response.status_code == 429:
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=str(response.url),
            error="http_429",
            retryable=False,
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
                "selected_url_source": i.selected_url_source,
                "resolution_attempts": i.resolution_attempts,
                "reason_code": i.reason_code,
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


def _coverage_report_file_path(acq_run_id: str) -> Path:
    return Path(settings.artifacts_dir) / "acquisition" / acq_run_id / "acquisition_coverage_report.json"


def _write_coverage_report_file(acq_run_id: str, observability_snapshot: dict) -> Path:
    counters = dict(observability_snapshot.get("counters", {}))
    payload = {
        "acq_run_id": acq_run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "coverage": {
            "resolved_via_openalex": int(counters.get("resolved_via_openalex", 0)),
            "resolved_via_unpaywall": int(counters.get("resolved_via_unpaywall", 0)),
            "resolved_via_repository": int(counters.get("resolved_via_repository", 0)),
            "paywalled": int(counters.get("paywalled", 0)),
            "manual_recovery_required": int(counters.get("manual_recovery_required", 0)),
        },
    }
    path = _coverage_report_file_path(acq_run_id)
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
                "legal_candidates": list(item.resolution_attempts or []),
                "reason_code": item.reason_code or _reason_code_from_error(item.last_error),
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
    item.reason_code = None
    item.updated_at = datetime.now(UTC)
    _recompute_acquisition_totals(db, run)
    db.commit()
    db.refresh(artifact)
    return artifact


def _manual_url_candidates(*, source: Source, item: AcquisitionItem) -> list[str]:
    ranked = sorted(
        list(item.resolution_attempts or []),
        key=lambda candidate: int(candidate.get("candidate_rank", 999999)),
    )
    urls = [candidate.get("candidate_url") for candidate in ranked]
    urls.extend([source.url, f"https://doi.org/{source.doi}" if source.doi else None, item.selected_url])
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
