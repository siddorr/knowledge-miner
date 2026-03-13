from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..models import DocumentChunk, ParseRun, ParsedDocument
from ..parse import create_parse_run, enqueue_parse_run
from ..rate_limit import require_rate_limit
from ..schemas import (
    DocumentChunkOut,
    DocumentChunksListResponse,
    ParseRunCreateRequest,
    ParseRunCreateResponse,
    ParseRunStatusResponse,
    ParsedDocumentOut,
    ParsedDocumentsListResponse,
    ParsedDocumentTextResponse,
)

router = APIRouter(tags=["parse"])


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


def _enqueue_parse_task(background_tasks: BackgroundTasks, parse_run_id: str) -> None:
    try:
        from .. import main as main_module

        enqueue_fn = getattr(main_module, "enqueue_parse_run", enqueue_parse_run)
    except Exception:
        enqueue_fn = enqueue_parse_run
    background_tasks.add_task(enqueue_fn, parse_run_id)


@router.post("/v1/parse/runs", response_model=ParseRunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
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
    _enqueue_parse_task(background_tasks, run.id)
    return ParseRunCreateResponse(parse_run_id=run.id, status=run.status)


@router.get("/v1/parse/runs/{parse_run_id}", response_model=ParseRunStatusResponse)
def get_parse_status(
    parse_run_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> ParseRunStatusResponse:
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


@router.get("/v1/parse/runs/{parse_run_id}/documents", response_model=ParsedDocumentsListResponse)
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


@router.get("/v1/parse/runs/{parse_run_id}/chunks", response_model=DocumentChunksListResponse)
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


@router.get("/v1/parse/documents/{document_id}", response_model=ParsedDocumentOut)
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


@router.get("/v1/parse/documents/{document_id}/text", response_model=ParsedDocumentTextResponse)
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
