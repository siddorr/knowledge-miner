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
from .routes.parse import router as parse_router
from .routes.acquisition import router as acquisition_router
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
app.include_router(parse_router)
app.include_router(acquisition_router)


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

