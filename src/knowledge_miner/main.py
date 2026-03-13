from __future__ import annotations

import logging
import asyncio
import time
from collections import defaultdict, deque
from pathlib import Path
import csv
import io
import base64
import json
import hashlib
import re
import os
from contextlib import suppress
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .acquisition import (
    build_manifest_payload,
    build_manual_downloads_payload,
    create_acquisition_run,
    enqueue_acquisition_run,
    mark_manual_complete,
    register_manual_upload,
)
from .ai_filter import describe_ai_filter_runtime
from .auth import require_api_key
from .config import is_sqlite_url, settings
from .db import Base, SessionLocal, database_readiness, engine, get_db
from .discovery import enqueue_run  # compatibility export for tests/patching
from .models import AcquisitionItem, AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Run, Source
from .parse import create_parse_run, enqueue_parse_run
from .rate_limit import require_rate_limit
from .logging_setup import configure_logging
from .runtime_state import acquire_instance_lock, cleanup_runtime_state, log_cleanup_result
from .routes.discovery import router as discovery_router
from .routes.hmi import router as hmi_router
from .routes.search import router as search_router
from .routes.settings import router as settings_router
from .routes.system import router as system_router
from .schemas import (
    AcquisitionItemsListResponse,
    AcquisitionItemOut,
    AcquisitionManifestResponse,
    AcquisitionRunCreateRequest,
    AcquisitionRunCreateResponse,
    AcquisitionRunStatusResponse,
    ArtifactOut,
    ManualDownloadItemOut,
    ManualDownloadsListResponse,
    ManualUploadRequest,
    ManualUploadResponse,
    ManualCompleteRequest,
    BatchUploadResponse,
    BatchUploadMatchOut,
    DocumentChunksListResponse,
    DocumentChunkOut,
    ParsedDocumentOut,
    ParsedDocumentsListResponse,
    ParsedDocumentTextResponse,
    ParseRunCreateRequest,
    ParseRunCreateResponse,
    ParseRunStatusResponse,
    RunStatusResponse,
    SystemStatusResponse,
    SourceOut,
    SourcesListResponse,
)

app = FastAPI(title="UPW Literature Discovery Engine", version="0.1.0")
logger = logging.getLogger("knowledge_miner")
HMI_DIR = Path(__file__).resolve().parent / "hmi"
HOT_READ_LIMIT_WINDOW_SECONDS = 10.0
HOT_READ_LIMIT_COUNT = 120
HOT_READ_WARN_COUNT = 60
_hot_read_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_hot_read_metrics: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "limited": 0})

# Create tables on module load for v1 local/dev simplicity.
app.mount("/hmi/static", StaticFiles(directory=HMI_DIR / "static"), name="hmi_static")
app.include_router(discovery_router)
app.include_router(hmi_router)
app.include_router(search_router)
app.include_router(settings_router)
app.include_router(system_router)


@app.on_event("startup")
def validate_runtime_config() -> None:
    log_path = configure_logging()
    logger.info("Persistent logging initialized at %s", log_path)
    db_meta = database_readiness()
    logger.info(
        "startup_db_context pid=%s ppid=%s cwd=%s process_role=%s database_url=%s sqlite_file=%s sqlite_inode=%s sqlite_mtime=%s ready=%s missing_tables=%s",
        os.getpid(),
        os.getppid(),
        os.getcwd(),
        _process_role(),
        db_meta["database_url"],
        db_meta["sqlite_file_path"] or "-",
        db_meta.get("sqlite_file_inode"),
        db_meta.get("sqlite_file_mtime"),
        db_meta["ready"],
        ",".join(db_meta["missing_tables"]) if db_meta["missing_tables"] else "-",
    )
    if (not db_meta["ready"]) and settings.db_auto_migrate_on_start:
        with suppress(Exception):
            Base.metadata.create_all(bind=engine)
        db_meta = database_readiness()
        logger.info(
            "DB auto-migrate check: enabled=%s ready=%s missing_tables=%s",
            settings.db_auto_migrate_on_start,
            db_meta["ready"],
            ",".join(db_meta["missing_tables"]) if db_meta["missing_tables"] else "-",
        )
    if not db_meta["ready"]:
        logger.error(
            "DB schema readiness failed: missing_tables=%s error=%s",
            ",".join(db_meta["missing_tables"]) if db_meta["missing_tables"] else "-",
            db_meta["error"] or "-",
        )
    cleanup_result = cleanup_runtime_state(base_dir=settings.runtime_state_dir, enabled=settings.clean_on_startup)
    log_cleanup_result(cleanup_result)
    primary = acquire_instance_lock(base_dir=settings.runtime_state_dir)
    if primary:
        logger.info("Primary runtime instance lock acquired.")
    else:
        logger.warning("Secondary runtime instance detected; background run workers are disabled in this process.")
    if settings.app_env.lower() in {"production", "prod"} and is_sqlite_url(settings.database_url):
        logger.warning(
            "Production mode is configured with SQLite. Use PostgreSQL DATABASE_URL for v1 production baseline."
        )


@app.exception_handler(OperationalError)
def handle_operational_error(_: Request, exc: OperationalError):
    detail = str(exc).lower()
    if "no such table" in detail:
        logger.error("Database schema not ready during request: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "database_not_ready_schema_missing",
                "hint": "Run migrations or enable DB_AUTO_MIGRATE_ON_START for local development.",
            },
        )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "database_error"})


def _process_role() -> str:
    if os.getenv("UVICORN_RELOAD_PROCESS", "").strip().lower() == "true":
        return "reloader"
    if os.getenv("RUN_MAIN", "").strip().lower() == "true":
        return "worker"
    return "single"


def _not_found_diagnostics(db: Session, *, run_id: str | None = None, source_id: str | None = None) -> dict:
    run_count = db.scalar(select(func.count()).select_from(Run)) or 0
    source_count = db.scalar(select(func.count()).select_from(Source)) or 0
    latest_run_ids = db.scalars(select(Run.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(5)).all()
    source_exists = bool(source_id and db.get(Source, source_id) is not None)
    db_meta = database_readiness()
    return {
        "run_id": run_id,
        "source_id": source_id,
        "run_count": int(run_count),
        "source_count": int(source_count),
        "latest_run_ids": latest_run_ids,
        "source_exists_any_run": source_exists,
        "db_file": db_meta.get("sqlite_file_path"),
        "db_inode": db_meta.get("sqlite_file_inode"),
        "pid": os.getpid(),
    }


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next):
    request_id = f"req_{uuid4().hex[:12]}"
    request.state.request_id = request_id
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/v1/discovery/runs") or path.startswith("/v1/sources/"):
        db_meta = database_readiness()
        logger.info(
            "request_trace request_id=%s pid=%s method=%s path=%s run_id=%s source_id=%s db_file=%s db_inode=%s status=%s",
            request_id,
            os.getpid(),
            request.method,
            path,
            request.path_params.get("run_id"),
            request.path_params.get("source_id"),
            db_meta.get("sqlite_file_path"),
            db_meta.get("sqlite_file_inode"),
            response.status_code,
        )
    return response


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return None


def _stage_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "queued":
        return "queued"
    if normalized == "running":
        return "running"
    if normalized == "completed":
        return "completed"
    if normalized == "failed":
        return "failed"
    return "idle"


def _extract_doi(text: str) -> str | None:
    match = re.search(r"(10\.\d{4,9}/[-._;()/:a-z0-9]+)", text.lower())
    if not match:
        return None
    return match.group(1).rstrip(").,;")


def _title_tokens(value: str) -> set[str]:
    parts = re.split(r"[^a-z0-9]+", value.lower())
    return {part for part in parts if len(part) >= 3}


def _hot_read_client_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        token_tail = token[-6:] if token else "none"
    else:
        token_tail = "none"
    return f"{ip}:{token_tail}"


def _guard_hot_read(request: Request, endpoint_name: str) -> None:
    key = (_hot_read_client_key(request), endpoint_name)
    now = time.time()
    bucket = _hot_read_buckets[key]
    cutoff = now - HOT_READ_LIMIT_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    bucket.append(now)
    metric = _hot_read_metrics[endpoint_name]
    metric["total"] += 1
    count = len(bucket)
    if count >= HOT_READ_WARN_COUNT:
        logger.warning(
            "hot_read_cadence_warning endpoint=%s client=%s count=%s window=%ss total=%s",
            endpoint_name,
            key[0],
            count,
            HOT_READ_LIMIT_WINDOW_SECONDS,
            metric["total"],
        )
    if count > HOT_READ_LIMIT_COUNT:
        metric["limited"] += 1
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="read_rate_limited")


def _authorize_event_stream(api_key: str | None) -> None:
    if not settings.auth_enabled:
        return
    expected = {value for value in [settings.api_token, settings.hmi_api_token] if value}
    if not expected or api_key not in expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def _collect_live_snapshot(db: Session) -> dict:
    latest_discovery = db.scalars(select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(1)).first()
    latest_acq = db.scalars(select(AcquisitionRun).order_by(AcquisitionRun.created_at.desc(), AcquisitionRun.id.desc()).limit(1)).first()
    latest_parse = db.scalars(select(ParseRun).order_by(ParseRun.created_at.desc(), ParseRun.id.desc()).limit(1)).first()
    pending_review = db.scalar(select(func.count()).select_from(Source).where(Source.review_status == "needs_review")) or 0
    doc_issues = (
        db.scalar(
            select(func.count()).select_from(AcquisitionItem).where(AcquisitionItem.status.in_(("failed", "partial")))
        )
        or 0
    )
    return {
        "latest_discovery": latest_discovery.id if latest_discovery else "",
        "latest_discovery_status": latest_discovery.status if latest_discovery else "idle",
        "latest_acquisition": latest_acq.id if latest_acq else "",
        "latest_acquisition_status": latest_acq.status if latest_acq else "idle",
        "latest_parse": latest_parse.id if latest_parse else "",
        "latest_parse_status": latest_parse.status if latest_parse else "idle",
        "pending_review": int(pending_review),
        "doc_issues": int(doc_issues),
    }


def _detect_live_events(previous: dict | None, current: dict) -> list[tuple[str, dict]]:
    if previous is None:
        return [("queue_updated", current)]
    events: list[tuple[str, dict]] = []

    def _run_event(prefix: str) -> None:
        prev_id = previous.get(f"latest_{prefix}", "")
        curr_id = current.get(f"latest_{prefix}", "")
        prev_status = previous.get(f"latest_{prefix}_status", "idle")
        curr_status = current.get(f"latest_{prefix}_status", "idle")
        if curr_id and curr_id != prev_id and curr_status in {"queued", "running"}:
            events.append(("run_started", {"phase": prefix, **current}))
        elif curr_id and curr_id == prev_id and curr_status != prev_status:
            if curr_status in {"completed", "failed"}:
                events.append(("run_completed", {"phase": prefix, **current}))
            else:
                events.append(("run_progress", {"phase": prefix, **current}))

    for phase in ("discovery", "acquisition", "parse"):
        _run_event(phase)

    if (
        current.get("pending_review") != previous.get("pending_review")
        or current.get("doc_issues") != previous.get("doc_issues")
    ):
        events.append(("queue_updated", current))
    return events


def _format_sse(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


@app.get("/v1/system/status", response_model=SystemStatusResponse)
def get_system_status(
    request: Request,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> Response:
    _guard_hot_read(request, "system_status")
    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=settings.use_ai_filter,
        api_key=settings.ai_api_key,
    )
    provider_readiness = {
        "openalex": {"configured": bool(settings.openalex_base_url)},
        "semantic_scholar": {
            "enabled": settings.use_semantic_scholar,
            "configured": bool(settings.use_semantic_scholar and settings.semantic_scholar_base_url),
            "api_key_present": bool(settings.semantic_scholar_api_key),
        },
        "brave": {
            "configured": bool(settings.brave_base_url),
            "api_key_present": bool(settings.brave_api_key),
        },
    }
    db_meta = database_readiness()
    run_count: int | None = None
    if db_meta["ready"]:
        with suppress(Exception):
            run_count = int(db.scalar(select(func.count()).select_from(Run)) or 0)
    payload = SystemStatusResponse(
        auth_enabled=settings.auth_enabled,
        auth_mode="enabled" if settings.auth_enabled else "disabled",
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
        provider_readiness=provider_readiness,
        db_ready=bool(db_meta["ready"]),
        db_missing_tables=list(db_meta["missing_tables"]),
        db_error=db_meta["error"],
        database_target=db_meta["sqlite_file_path"] or settings.database_url,
        db_target_url=settings.database_url,
        db_target_resolved_path=db_meta["sqlite_file_path"],
        db_schema_ready=bool(db_meta["ready"]),
        db_run_count=run_count,
        process_pid=os.getpid(),
        hot_read_metrics={k: dict(v) for k, v in _hot_read_metrics.items()},
    )
    stable_payload = payload.model_dump()
    stable_payload["hot_read_metrics"] = {}
    etag = hashlib.sha256(json.dumps(stable_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return JSONResponse(content=payload.model_dump(), headers={"ETag": etag})


@app.get("/v1/events/stream")
async def stream_hmi_events(
    request: Request,
    api_key: str | None = Query(default=None),
    once: bool = Query(default=False),
) -> StreamingResponse:
    _authorize_event_stream(api_key)

    async def event_generator():
        previous: dict | None = None
        yield _format_sse("connected", {"status": "connected"})
        if once:
            try:
                with SessionLocal() as db:
                    current = _collect_live_snapshot(db)
                yield _format_sse("queue_updated", current)
            except Exception as exc:
                yield _format_sse("error", {"message": str(exc)})
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                with SessionLocal() as db:
                    current = _collect_live_snapshot(db)
                for event_name, payload in _detect_live_events(previous, current):
                    yield _format_sse(event_name, payload)
                previous = current
            except Exception as exc:
                yield _format_sse("error", {"message": str(exc)})
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/v1/discovery/runs/{run_id}", response_model=RunStatusResponse)
def get_run_status(
    run_id: str,
    request: Request,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunStatusResponse:
    _guard_hot_read(request, "discovery_run_status")
    run = db.get(Run, run_id)
    if run is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    pending_review = db.scalar(
        select(func.count()).select_from(Source).where(Source.run_id == run_id, Source.review_status == "needs_review")
    ) or 0
    stage_status = _stage_status(run.status)
    if stage_status == "completed" and pending_review > 0:
        stage_status = "waiting_user"
    total_steps = max(int(run.max_iterations or 0), 1)
    completed_steps = min(int(run.current_iteration or 0), total_steps)
    percent = round((completed_steps / total_steps) * 100.0, 1) if total_steps > 0 else None
    if stage_status == "queued":
        message = "Queued to start source discovery."
    elif stage_status == "running":
        message = "Searching sources and evaluating relevance."
    elif stage_status == "waiting_user":
        message = "Waiting for review decisions."
    elif stage_status == "failed":
        message = run.error_message or "Discovery failed."
    else:
        message = "Discovery completed."
    ai_filter_effective_enabled = bool(run.ai_filter_active and settings.ai_api_key)
    return RunStatusResponse(
        run_id=run.id,
        status=run.status,
        seed_queries=run.seed_queries,
        current_iteration=run.current_iteration,
        accepted_total=run.accepted_total,
        expanded_candidates_total=run.expanded_candidates_total,
        citation_edges_total=run.citation_edges_total,
        ai_filter_active=run.ai_filter_active,
        ai_filter_warning=run.ai_filter_warning,
        ai_filter_effective_enabled=ai_filter_effective_enabled,
        ai_filter_config_source="run",
        new_accept_rate=float(run.new_accept_rate) if run.new_accept_rate is not None else None,
        current_stage="discovery",
        stage_status=stage_status,
        completed=completed_steps,
        total=total_steps,
        percent=percent,
        message=message,
        started_at=_iso_or_none(run.created_at),
        updated_at=_iso_or_none(run.updated_at),
    )


@app.get("/v1/discovery/runs/{run_id}/sources", response_model=SourcesListResponse)
def list_sources(
    run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    min_score: float | None = Query(default=None, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SourcesListResponse:
    _guard_hot_read(request, "discovery_sources")
    run = db.get(Run, run_id)
    if run is None:
        logger.warning("run_not_found %s", _not_found_diagnostics(db, run_id=run_id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")

    stmt = select(Source).where(Source.run_id == run_id)
    effective_status = (status_filter or "accepted").strip().lower()
    if effective_status == "accepted":
        stmt = stmt.where(Source.accepted.is_(True))
    elif effective_status == "rejected":
        stmt = stmt.where(Source.review_status.in_(("auto_reject", "human_reject")))
    elif effective_status == "needs_review":
        stmt = stmt.where(Source.review_status == "needs_review")
    elif effective_status == "later":
        stmt = stmt.where(Source.review_status == "human_later")
    elif effective_status == "all":
        pass
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")
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
                final_decision=s.final_decision,
                decision_source=s.decision_source,
                heuristic_recommendation=s.heuristic_recommendation,
                heuristic_score=float(s.heuristic_score),
                parent_source=s.parent_source_id,
            )
            for s in page
        ],
        total=len(all_rows),
        limit=limit,
        offset=offset,
    )


@app.post("/v1/acquisition/runs", response_model=AcquisitionRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
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

    background_tasks.add_task(enqueue_acquisition_run, run.id)
    return AcquisitionRunCreateResponse(acq_run_id=run.id, status=run.status)


@app.get("/v1/acquisition/runs/{acq_run_id}", response_model=AcquisitionRunStatusResponse)
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


@app.get("/v1/acquisition/runs/{acq_run_id}/items", response_model=AcquisitionItemsListResponse)
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


@app.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads", response_model=ManualDownloadsListResponse)
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


@app.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads.csv")
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
    writer.writerow(
        [
            "item_id",
            "source_id",
            "status",
            "attempt_count",
            "last_error",
            "title",
            "doi",
            "source_url",
            "selected_url",
            "manual_url_candidates",
            "reason_code",
            "legal_candidates",
        ]
    )
    for item in payload["items"]:
        writer.writerow(
            [
                item["item_id"],
                item["source_id"],
                item["status"],
                item["attempt_count"],
                item["last_error"] or "",
                item["title"],
                item["doi"] or "",
                item["source_url"] or "",
                item["selected_url"] or "",
                " | ".join(item["manual_url_candidates"]),
                item.get("reason_code") or "",
                " | ".join(
                    f"{c.get('candidate_rank')}:{c.get('candidate_source')}:{c.get('candidate_url')}"
                    for c in item.get("legal_candidates", [])
                ),
            ]
        )

    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="manual_downloads_{acq_run_id}.csv"',
        },
    )


@app.post("/v1/acquisition/runs/{acq_run_id}/manual-upload", response_model=ManualUploadResponse)
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


@app.post("/v1/acquisition/runs/{acq_run_id}/manual-complete")
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


@app.post("/v1/acquisition/runs/{acq_run_id}/manual-upload-batch", response_model=BatchUploadResponse)
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


@app.post("/v1/parse/runs", response_model=ParseRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_parse(
    payload: ParseRunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParseRunCreateResponse:
    try:
        run = create_parse_run(db, payload.acq_run_id, retry_failed_only=payload.retry_failed_only)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_complete") from exc
    background_tasks.add_task(enqueue_parse_run, run.id)
    return ParseRunCreateResponse(parse_run_id=run.id, status=run.status)


@app.get("/v1/parse/runs/{parse_run_id}", response_model=ParseRunStatusResponse)
def get_parse_status(
    parse_run_id: str,
    request: Request,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParseRunStatusResponse:
    _guard_hot_read(request, "parse_run_status")
    run = db.get(ParseRun, parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    total = max(int(run.total_documents or 0), 1)
    completed = int((run.parsed_total or 0) + (run.failed_total or 0))
    completed = min(completed, total)
    percent = round((completed / total) * 100.0, 1) if total > 0 else None
    stage_status = _stage_status(run.status)
    if stage_status == "queued":
        message = "Queued to parse documents."
    elif stage_status == "running":
        message = "Parsing and chunking documents."
    elif stage_status == "failed":
        message = run.error_message or "Parse failed."
    else:
        message = "Parse completed."
    return ParseRunStatusResponse(
        parse_run_id=run.id,
        acq_run_id=run.acq_run_id,
        retry_failed_only=run.retry_failed_only,
        ai_filter_active=run.ai_filter_active,
        ai_filter_warning=run.ai_filter_warning,
        status=run.status,
        total_documents=run.total_documents,
        parsed_total=run.parsed_total,
        failed_total=run.failed_total,
        chunked_total=run.chunked_total,
        error_message=run.error_message,
        current_stage="parse",
        stage_status=stage_status,
        completed=completed,
        total=total,
        percent=percent,
        message=message,
        started_at=_iso_or_none(run.created_at),
        updated_at=_iso_or_none(run.updated_at),
    )


@app.get("/v1/parse/runs/{parse_run_id}/documents", response_model=ParsedDocumentsListResponse)
def list_parse_documents(
    parse_run_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParsedDocumentsListResponse:
    run = db.get(ParseRun, parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    rows = db.scalars(
        select(ParsedDocument).where(ParsedDocument.parse_run_id == parse_run_id).order_by(ParsedDocument.id.asc())
    ).all()
    page = rows[offset : offset + limit]
    return ParsedDocumentsListResponse(
        items=[
            ParsedDocumentOut(
                document_id=d.id,
                source_id=d.source_id,
                artifact_id=d.artifact_id,
                status=d.status,
                title=d.title,
                publication_year=d.publication_year,
                language=d.language,
                parser_used=d.parser_used,
                relevance_score=float(d.relevance_score) if d.relevance_score is not None else None,
                decision=d.decision,
                confidence=float(d.confidence) if d.confidence is not None else None,
                reason=d.reason,
                char_count=d.char_count,
                section_count=d.section_count,
                last_error=d.last_error,
            )
            for d in page
        ],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@app.get("/v1/parse/runs/{parse_run_id}/chunks", response_model=DocumentChunksListResponse)
def list_parse_chunks(
    parse_run_id: str,
    document_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> DocumentChunksListResponse:
    run = db.get(ParseRun, parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    stmt = select(DocumentChunk).where(DocumentChunk.parse_run_id == parse_run_id)
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.parsed_document_id == document_id)
    rows = db.scalars(stmt.order_by(DocumentChunk.parsed_document_id.asc(), DocumentChunk.chunk_index.asc())).all()
    page = rows[offset : offset + limit]
    return DocumentChunksListResponse(
        items=[
            DocumentChunkOut(
                chunk_id=c.id,
                document_id=c.parsed_document_id,
                chunk_index=c.chunk_index,
                relevance_score=float(c.relevance_score) if c.relevance_score is not None else None,
                decision=c.decision,
                confidence=float(c.confidence) if c.confidence is not None else None,
                reason=c.reason,
                start_char=c.start_char,
                end_char=c.end_char,
                text=c.text,
            )
            for c in page
        ],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@app.get("/v1/parse/documents/{document_id}", response_model=ParsedDocumentOut)
def get_parsed_document(
    document_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParsedDocumentOut:
    doc = db.get(ParsedDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document_not_found")
    return ParsedDocumentOut(
        document_id=doc.id,
        source_id=doc.source_id,
        artifact_id=doc.artifact_id,
        status=doc.status,
        title=doc.title,
        publication_year=doc.publication_year,
        language=doc.language,
        parser_used=doc.parser_used,
        relevance_score=float(doc.relevance_score) if doc.relevance_score is not None else None,
        decision=doc.decision,
        confidence=float(doc.confidence) if doc.confidence is not None else None,
        reason=doc.reason,
        char_count=doc.char_count,
        section_count=doc.section_count,
        last_error=doc.last_error,
    )


@app.get("/v1/parse/documents/{document_id}/text", response_model=ParsedDocumentTextResponse)
def get_parsed_document_text(
    document_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParsedDocumentTextResponse:
    doc = db.get(ParsedDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document_not_found")
    if not doc.body_text:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="document_not_parsed")
    return ParsedDocumentTextResponse(document_id=doc.id, text=doc.body_text)
