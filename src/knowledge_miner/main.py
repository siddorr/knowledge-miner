from __future__ import annotations

import logging
from pathlib import Path
import csv
import io
import base64
import json
import re
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .acquisition import (
    build_manifest_payload,
    build_manual_downloads_payload,
    create_acquisition_run,
    enqueue_acquisition_run,
    register_manual_upload,
)
from .ai_filter import describe_ai_filter_runtime
from .auth import require_api_key
from .config import is_sqlite_url, settings
from .db import Base, engine, get_db
from .discovery import create_run, enqueue_run, export_sources_raw, review_source
from .models import AcquisitionItem, AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Run, Source
from .parse import create_parse_run, enqueue_parse_run
from .rate_limit import require_rate_limit
from .logging_setup import configure_logging
from .schemas import (
    AcquisitionItemsListResponse,
    AcquisitionItemOut,
    AcquisitionManifestResponse,
    AcquisitionRunCreateRequest,
    AcquisitionRunCreateResponse,
    AcquisitionRunStatusResponse,
    ArtifactOut,
    AISettingsResponse,
    AISettingsUpdateRequest,
    ManualDownloadItemOut,
    ManualDownloadsListResponse,
    ManualUploadRequest,
    ManualUploadResponse,
    DocumentChunksListResponse,
    DocumentChunkOut,
    ParsedDocumentOut,
    ParsedDocumentsListResponse,
    ParsedDocumentTextResponse,
    ParseRunCreateRequest,
    ParseRunCreateResponse,
    ParseRunStatusResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunStatusResponse,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
    SourceOut,
    SourceReviewRequest,
    SourceReviewResponse,
    SourcesListResponse,
)

app = FastAPI(title="UPW Literature Discovery Engine", version="0.1.0")
logger = logging.getLogger("knowledge_miner")
HMI_DIR = Path(__file__).resolve().parent / "hmi"

# Create tables on module load for v1 local/dev simplicity.
Base.metadata.create_all(bind=engine)
app.mount("/hmi/static", StaticFiles(directory=HMI_DIR / "static"), name="hmi_static")


@app.on_event("startup")
def validate_runtime_config() -> None:
    log_path = configure_logging()
    logger.info("Persistent logging initialized at %s", log_path)
    if settings.app_env.lower() in {"production", "prod"} and is_sqlite_url(settings.database_url):
        logger.warning(
            "Production mode is configured with SQLite. Use PostgreSQL DATABASE_URL for v1 production baseline."
        )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _build_ai_settings_response() -> AISettingsResponse:
    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=settings.use_ai_filter,
        api_key=settings.ai_api_key,
    )
    return AISettingsResponse(
        use_ai_filter=settings.use_ai_filter,
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
        has_api_key=bool(settings.ai_api_key),
        api_key_masked=_mask_api_key(settings.ai_api_key),
        ai_model=settings.ai_model,
        ai_base_url=settings.ai_base_url,
    )


def _validate_ai_model(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", value))


def _validate_ai_base_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@app.get("/v1/settings/ai-filter", response_model=AISettingsResponse)
def get_ai_filter_settings(
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> AISettingsResponse:
    return _build_ai_settings_response()


@app.post("/v1/settings/ai-filter", response_model=AISettingsResponse)
def update_ai_filter_settings(
    payload: AISettingsUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> AISettingsResponse:
    provided = payload.model_fields_set
    if "use_ai_filter" in provided and payload.use_ai_filter is not None:
        object.__setattr__(settings, "use_ai_filter", bool(payload.use_ai_filter))
    if "ai_api_key" in provided:
        normalized = (payload.ai_api_key or "").strip()
        object.__setattr__(settings, "ai_api_key", normalized or None)
    if "ai_model" in provided:
        model = (payload.ai_model or "").strip()
        if model and not _validate_ai_model(model):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")
        if model:
            object.__setattr__(settings, "ai_model", model)
    if "ai_base_url" in provided:
        base_url = (payload.ai_base_url or "").strip()
        if base_url and not _validate_ai_base_url(base_url):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")
        if base_url:
            object.__setattr__(settings, "ai_base_url", base_url)
    return _build_ai_settings_response()


@app.get("/hmi")
def hmi_shell() -> HTMLResponse:
    template = (HMI_DIR / "index.html").read_text(encoding="utf-8")
    token_json = json.dumps(settings.hmi_api_token) if settings.auth_enabled and settings.hmi_api_token else "null"
    auth_enabled_json = "true" if settings.auth_enabled else "false"
    html = template.replace("__HMI_DEFAULT_TOKEN_JSON__", token_json).replace("__HMI_AUTH_ENABLED__", auth_enabled_json)
    return HTMLResponse(content=html)


@app.post("/v1/discovery/runs", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_discovery_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> RunCreateResponse:
    run = create_run(db, payload.seed_queries, payload.max_iterations, ai_filter_enabled=payload.ai_filter_enabled)
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
        seed_queries=run.seed_queries,
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
    status_filter: str | None = Query(default=None, alias="status"),
    min_score: float | None = Query(default=None, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SourcesListResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")

    stmt = select(Source).where(Source.run_id == run_id)
    effective_status = (status_filter or "accepted").strip().lower()
    if effective_status == "accepted":
        stmt = stmt.where(Source.accepted.is_(True))
    elif effective_status == "rejected":
        stmt = stmt.where(Source.accepted.is_(False), Source.review_status != "needs_review")
    elif effective_status == "needs_review":
        stmt = stmt.where(Source.review_status == "needs_review")
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


@app.post("/v1/sources/{source_id:path}/review", response_model=SourceReviewResponse)
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


@app.get("/v1/acquisition/runs/{acq_run_id}/manual-downloads", response_model=ManualDownloadsListResponse)
def list_manual_downloads(
    acq_run_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ManualDownloadsListResponse:
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
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParseRunStatusResponse:
    run = db.get(ParseRun, parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
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


@app.post("/v1/search", response_model=SearchResponse)
def search_corpus(
    payload: SearchRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SearchResponse:
    run = db.get(ParseRun, payload.parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    needle = payload.query.strip().lower()
    if not needle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")

    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.parse_run_id == payload.parse_run_id).order_by(DocumentChunk.id.asc())
    ).all()
    scored: list[tuple[DocumentChunk, float]] = []
    for chunk in chunks:
        hay = chunk.text.lower()
        hits = hay.count(needle)
        if hits <= 0:
            continue
        score = float(hits)
        scored.append((chunk, score))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    page = scored[: payload.limit]

    docs = {doc.id: doc for doc in db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == payload.parse_run_id)).all()}
    return SearchResponse(
        items=[
            SearchResultOut(
                document_id=chunk.parsed_document_id,
                chunk_id=chunk.id,
                source_id=docs[chunk.parsed_document_id].source_id if chunk.parsed_document_id in docs else "",
                score=score,
                snippet=chunk.text[:300],
            )
            for chunk, score in page
        ],
        total=len(scored),
    )
