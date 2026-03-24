from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_filter import generate_paper_summary
from ..auth import require_api_key
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import PaperAnnotation, ParsedDocument, Run, SessionProfile, SessionSummarySettings, SessionTagCatalog, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    PaperAnnotationOut,
    PaperAnnotationsListResponse,
    PaperAnnotationUpdateRequest,
    SessionSummarySettingsOut,
    SessionSummarySettingsUpdateRequest,
    SessionTagCatalogOut,
    SessionTagCatalogUpdateRequest,
    SummaryGenerationBlockedOut,
    SummaryGenerationRequest,
    SummaryGenerationResponse,
)

router = APIRouter(tags=["annotations"])
logger = logging.getLogger("knowledge_miner")

DEFAULT_SUMMARY_PROMPT = (
    "Write a concise research-context summary for this paper. Focus on: "
    "the problem addressed, the treatment/process/technology, the key findings, "
    "why it matters to this research session, and any obvious limitations or uncertainty."
)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _normalize_tag(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_tag_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = _normalize_tag(raw)
        if not tag:
            continue
        tag = tag[:64]
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
        if len(normalized) >= 30:
            break
    return normalized


def _session_exists(db: Session, session_id: str) -> bool:
    return db.get(SessionProfile, session_id) is not None or db.scalar(select(Run.id).where(Run.session_id == session_id).limit(1)) is not None


def _session_run_ids(db: Session, session_id: str) -> list[str]:
    return db.scalars(select(Run.id).where(Run.session_id == session_id)).all()


def _session_source_map(db: Session, session_id: str, source_ids: list[str]) -> dict[str, Source]:
    if not source_ids:
        return {}
    run_ids = _session_run_ids(db, session_id)
    if not run_ids:
        return {}
    rows = db.scalars(
        select(Source).where(Source.run_id.in_(run_ids), Source.id.in_(source_ids), Source.accepted.is_(True))
    ).all()
    return {row.id: row for row in rows}


def _parsed_text_rows(db: Session, source_ids: list[str]) -> dict[str, ParsedDocument]:
    if not source_ids:
        return {}
    rows = db.scalars(
        select(ParsedDocument)
        .where(
            ParsedDocument.source_id.in_(source_ids),
            ParsedDocument.status == "parsed",
            ParsedDocument.body_text.is_not(None),
        )
        .order_by(ParsedDocument.updated_at.desc(), ParsedDocument.created_at.desc(), ParsedDocument.id.desc())
    ).all()
    latest: dict[str, ParsedDocument] = {}
    for row in rows:
        latest.setdefault(row.source_id, row)
    return latest


def _annotation_row(db: Session, session_id: str, source_id: str) -> PaperAnnotation | None:
    return db.scalars(
        select(PaperAnnotation).where(PaperAnnotation.session_id == session_id, PaperAnnotation.source_id == source_id).limit(1)
    ).first()


def _annotation_out(
    session_id: str,
    source_id: str,
    annotation: PaperAnnotation | None,
    *,
    parsed_ready: bool,
) -> PaperAnnotationOut:
    status_value = annotation.summary_status if annotation is not None else "none"
    block_reason = None if parsed_ready else "parsed_text_required"
    can_generate = parsed_ready and status_value not in {"queued", "running"}
    return PaperAnnotationOut(
        session_id=session_id,
        source_id=source_id,
        freeform_tags=list(annotation.freeform_tags_json or []) if annotation is not None else [],
        approved_tags=list(annotation.approved_tags_json or []) if annotation is not None else [],
        ai_summary=annotation.ai_summary if annotation is not None else None,
        summary_status=status_value,
        summary_generated_at=_iso_or_none(annotation.summary_generated_at) if annotation is not None else None,
        summary_error=annotation.summary_error if annotation is not None else None,
        can_generate_summary=can_generate,
        summary_block_reason=block_reason,
    )


def _get_or_create_annotation(db: Session, session_id: str, source_id: str) -> PaperAnnotation:
    existing = _annotation_row(db, session_id, source_id)
    if existing is not None:
        return existing
    created = PaperAnnotation(
        id=f"annot_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        source_id=source_id,
        freeform_tags_json=[],
        approved_tags_json=[],
        summary_status="none",
    )
    db.add(created)
    return created


def _summary_settings(db: Session, session_id: str) -> SessionSummarySettingsOut:
    row = db.get(SessionSummarySettings, session_id)
    prompt_template = row.prompt_template if row is not None else DEFAULT_SUMMARY_PROMPT
    return SessionSummarySettingsOut(session_id=session_id, prompt_template=prompt_template)


def _generate_summaries_task(session_id: str, source_ids: list[str], force_regenerate: bool) -> None:
    with SessionLocal() as db:
        profile = db.get(SessionProfile, session_id)
        session_context = profile.session_context if profile and profile.session_context else ""
        settings_row = db.get(SessionSummarySettings, session_id)
        default_prompt = settings_row.prompt_template if settings_row is not None else DEFAULT_SUMMARY_PROMPT
        parsed_rows = _parsed_text_rows(db, source_ids)
        for source_id in source_ids:
            annotation = _annotation_row(db, session_id, source_id)
            source = db.get(Source, source_id)
            parsed = parsed_rows.get(source_id)
            if annotation is None or source is None or parsed is None or not parsed.body_text:
                continue
            if annotation.summary_status == "completed" and not force_regenerate and annotation.ai_summary:
                continue
            prompt_template = annotation.summary_prompt_snapshot or default_prompt
            annotation.summary_status = "running"
            annotation.summary_error = None
            db.commit()
            try:
                result = generate_paper_summary(
                    session_context=session_context,
                    prompt_template=prompt_template,
                    title=source.title,
                    body_text=parsed.body_text,
                    doi=source.doi,
                    year=source.year,
                )
                annotation.ai_summary = result.summary
                annotation.summary_status = "completed"
                annotation.summary_model = settings.ai_model
                annotation.summary_generated_at = datetime.now(UTC)
                annotation.summary_error = None
                db.commit()
                logger.info("paper_summary_completed session_id=%s source_id=%s", session_id, source_id)
            except Exception as exc:
                db.rollback()
                annotation = _annotation_row(db, session_id, source_id)
                if annotation is None:
                    continue
                if not annotation.ai_summary:
                    annotation.summary_status = "failed"
                else:
                    annotation.summary_status = "completed"
                annotation.summary_error = str(exc)
                db.commit()
                logger.warning("paper_summary_failed session_id=%s source_id=%s error=%s", session_id, source_id, exc)


@router.get("/v1/sessions/{session_id}/annotations", response_model=PaperAnnotationsListResponse)
def list_annotations(
    session_id: str,
    source_id: list[str] | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationsListResponse:
    if not _session_exists(db, session_id):
        return PaperAnnotationsListResponse(items=[], total=0, limit=limit, offset=offset)
    requested_source_ids = [value for value in (source_id or []) if value]
    if requested_source_ids:
        source_map = _session_source_map(db, session_id, requested_source_ids)
        rows = db.scalars(
            select(PaperAnnotation).where(
                PaperAnnotation.session_id == session_id,
                PaperAnnotation.source_id.in_(requested_source_ids),
            )
        ).all()
        annotation_by_source = {row.source_id: row for row in rows}
        parsed_rows = _parsed_text_rows(db, list(source_map.keys()))
        items = [
            _annotation_out(
                session_id,
                source_id_value,
                annotation_by_source.get(source_id_value),
                parsed_ready=source_id_value in parsed_rows,
            )
            for source_id_value in requested_source_ids
            if source_id_value in source_map
        ]
    else:
        rows = db.scalars(
            select(PaperAnnotation)
            .where(PaperAnnotation.session_id == session_id)
            .order_by(PaperAnnotation.updated_at.desc(), PaperAnnotation.id.desc())
        ).all()
        parsed_rows = _parsed_text_rows(db, [row.source_id for row in rows])
        items = [
            _annotation_out(session_id, row.source_id, row, parsed_ready=row.source_id in parsed_rows)
            for row in rows
        ]
    page = items[offset : offset + limit]
    return PaperAnnotationsListResponse(items=page, total=len(items), limit=limit, offset=offset)


@router.put("/v1/sessions/{session_id}/annotations/{source_id}", response_model=PaperAnnotationOut)
def upsert_annotation(
    session_id: str,
    source_id: str,
    payload: PaperAnnotationUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> PaperAnnotationOut:
    source_map = _session_source_map(db, session_id, [source_id])
    if source_id not in source_map:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="paper_not_annotatable_in_session")
    approved_tags = _normalize_tag_list(payload.approved_tags)
    freeform_tags = _normalize_tag_list(payload.freeform_tags)
    approved_catalog = {
        row.tag.lower(): row.tag
        for row in db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
    }
    for tag in approved_tags:
        if tag.lower() not in approved_catalog:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approved_tag_not_in_catalog")
    annotation = _get_or_create_annotation(db, session_id, source_id)
    if payload.freeform_tags is not None:
        annotation.freeform_tags_json = freeform_tags
    if payload.approved_tags is not None:
        annotation.approved_tags_json = approved_tags
    db.commit()
    db.refresh(annotation)
    parsed_rows = _parsed_text_rows(db, [source_id])
    return _annotation_out(session_id, source_id, annotation, parsed_ready=source_id in parsed_rows)


@router.get("/v1/sessions/{session_id}/tag-catalog", response_model=SessionTagCatalogOut)
def get_tag_catalog(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagCatalogOut:
    tags = [row.tag for row in db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id).order_by(SessionTagCatalog.tag.asc())).all()]
    return SessionTagCatalogOut(session_id=session_id, tags=tags)


@router.put("/v1/sessions/{session_id}/tag-catalog", response_model=SessionTagCatalogOut)
def put_tag_catalog(
    session_id: str,
    payload: SessionTagCatalogUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionTagCatalogOut:
    tags = _normalize_tag_list(payload.tags)
    existing = db.scalars(select(SessionTagCatalog).where(SessionTagCatalog.session_id == session_id)).all()
    for row in existing:
        db.delete(row)
    for tag in tags:
        db.add(SessionTagCatalog(id=f"stag_{uuid.uuid4().hex[:12]}", session_id=session_id, tag=tag))
    db.commit()
    return SessionTagCatalogOut(session_id=session_id, tags=tags)


@router.get("/v1/sessions/{session_id}/summary-settings", response_model=SessionSummarySettingsOut)
def get_summary_settings(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionSummarySettingsOut:
    return _summary_settings(db, session_id)


@router.put("/v1/sessions/{session_id}/summary-settings", response_model=SessionSummarySettingsOut)
def put_summary_settings(
    session_id: str,
    payload: SessionSummarySettingsUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionSummarySettingsOut:
    row = db.get(SessionSummarySettings, session_id)
    if row is None:
        row = SessionSummarySettings(session_id=session_id, prompt_template=payload.prompt_template.strip())
        db.add(row)
    else:
        row.prompt_template = payload.prompt_template.strip()
    db.commit()
    return SessionSummarySettingsOut(session_id=session_id, prompt_template=row.prompt_template)


@router.post("/v1/sessions/{session_id}/summaries/generate", response_model=SummaryGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_summaries(
    session_id: str,
    payload: SummaryGenerationRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SummaryGenerationResponse:
    source_ids = [value for value in payload.source_ids if value]
    if not source_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="source_ids_required")
    source_map = _session_source_map(db, session_id, source_ids)
    parsed_rows = _parsed_text_rows(db, list(source_map.keys()))
    summary_settings = _summary_settings(db, session_id)
    blocked: list[SummaryGenerationBlockedOut] = []
    queued: list[str] = []
    for source_id in source_ids:
        if source_id not in source_map:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="paper_not_annotatable_in_session"))
            continue
        parsed = parsed_rows.get(source_id)
        if parsed is None or not parsed.body_text:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="parsed_text_required"))
            continue
        annotation = _get_or_create_annotation(db, session_id, source_id)
        if annotation.summary_status == "completed" and annotation.ai_summary and not payload.force_regenerate:
            blocked.append(SummaryGenerationBlockedOut(source_id=source_id, reason="already_generated"))
            continue
        annotation.summary_status = "queued"
        annotation.summary_error = None
        annotation.summary_prompt_snapshot = summary_settings.prompt_template
        queued.append(source_id)
    db.commit()
    if queued:
        background_tasks.add_task(_generate_summaries_task, session_id, queued, payload.force_regenerate)
        logger.info("paper_summary_queued session_id=%s queued_count=%s", session_id, len(queued))
    return SummaryGenerationResponse(
        session_id=session_id,
        queued_count=len(queued),
        blocked_count=len(blocked),
        blocked=blocked,
    )
