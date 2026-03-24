from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..config import settings
from ..db import get_db
from ..models import AcquisitionItem, Run, SessionProfile, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    HMIEventsIngestRequest,
    HMIEventsIngestResponse,
    SessionProfileResponse,
    SessionProfilesListResponse,
    SessionProfileUpsertRequest,
)

router = APIRouter(tags=["hmi"])
logger_name = "knowledge_miner"
HMI_V2_DIR = Path(__file__).resolve().parents[1] / "hmi_v2"


def _hmi_launch_section(db: Session) -> str:
    run_count = db.scalar(select(func.count()).select_from(Run)) or 0
    if run_count == 0:
        return "discover"
    review_count = db.scalar(
        select(func.count()).select_from(Source).where(Source.review_status == "needs_review")
    ) or 0
    if review_count > 0:
        return "review"
    failed_docs_count = db.scalar(
        select(func.count())
        .select_from(AcquisitionItem)
        .where(AcquisitionItem.status.in_(("failed", "partial")))
    ) or 0
    return "documents" if failed_docs_count > 0 else "discover"


def _hash_user_agent(user_agent: str | None) -> str:
    raw = (user_agent or "").strip()
    if not raw:
        return "none"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _sanitize_hmi_value_preview(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return ""
    lowered = trimmed.lower()
    if any(token in lowered for token in ("bearer ", "api_key", "password", "token", "sk-")):
        return "[redacted]"
    if len(trimmed) > 120:
        return f"{trimmed[:120]}..."
    return trimmed


@router.get("/hmi")
def hmi_shell(db: Session = Depends(get_db)) -> RedirectResponse:
    del db
    return RedirectResponse(url="/hmi2", status_code=307)


@router.get("/hmi2")
def hmi2_shell(db: Session = Depends(get_db)) -> HTMLResponse:
    launch_section = _hmi_launch_section(db)
    template = (HMI_V2_DIR / "index.html").read_text(encoding="utf-8")
    token_json = json.dumps(settings.hmi_api_token) if settings.auth_enabled and settings.hmi_api_token else "null"
    auth_enabled_json = "true" if settings.auth_enabled else "false"
    launch_section_json = json.dumps(launch_section)
    static_version = str(
        max(
            int((HMI_V2_DIR / "static" / "gui.js").stat().st_mtime),
            int((HMI_V2_DIR / "static" / "gui.css").stat().st_mtime),
        )
    )
    html = (
        template
        .replace("__HMI2_DEFAULT_TOKEN_JSON__", token_json)
        .replace("__HMI2_AUTH_ENABLED__", auth_enabled_json)
        .replace("__HMI2_LAUNCH_SECTION_JSON__", launch_section_json)
        .replace("__HMI2_STATIC_VERSION__", static_version)
    )
    return HTMLResponse(content=html)


@router.post("/v1/hmi/events", response_model=HMIEventsIngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_hmi_events(
    payload: HMIEventsIngestRequest,
    request: Request,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> HMIEventsIngestResponse:
    import logging
    logger = logging.getLogger(logger_name)

    ua_hash = _hash_user_agent(request.headers.get("user-agent"))
    for event in payload.events:
        record = {
            "event_type": event.event_type,
            "control_id": event.control_id,
            "control_label": event.control_label,
            "page": event.page,
            "section": event.section,
            "session_id": event.session_id,
            "run_id": event.run_id,
            "acq_run_id": event.acq_run_id,
            "parse_run_id": event.parse_run_id,
            "value_preview": _sanitize_hmi_value_preview(event.value_preview),
            "timestamp_ms": event.timestamp_ms,
            "ua_hash": ua_hash,
        }
        logger.info("hmi_event %s", json.dumps(record, sort_keys=True, ensure_ascii=True))
    return HMIEventsIngestResponse(accepted=len(payload.events))


@router.get("/v1/sessions/{session_id}", response_model=SessionProfileResponse)
def get_session_profile(
    session_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionProfileResponse:
    session_key = session_id.strip()
    if not session_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id_required")
    profile = db.get(SessionProfile, session_key)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return SessionProfileResponse(
        session_id=profile.session_id,
        name=profile.name,
        session_context=profile.session_context,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
    )


@router.get("/v1/sessions", response_model=SessionProfilesListResponse)
def list_session_profiles(
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionProfilesListResponse:
    rows = db.scalars(select(SessionProfile).order_by(SessionProfile.updated_at.desc(), SessionProfile.session_id.asc())).all()
    return SessionProfilesListResponse(
        items=[
            SessionProfileResponse(
                session_id=row.session_id,
                name=row.name,
                session_context=row.session_context,
                updated_at=row.updated_at.isoformat() if row.updated_at else None,
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.put("/v1/sessions/{session_id}", response_model=SessionProfileResponse)
def upsert_session_profile(
    session_id: str,
    payload: SessionProfileUpsertRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SessionProfileResponse:
    session_key = session_id.strip()
    if not session_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id_required")
    context = payload.session_context.strip()
    if not context:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_context_required")
    profile = db.get(SessionProfile, session_key)
    if profile is None:
        profile = SessionProfile(session_id=session_key)
        db.add(profile)
    profile.name = payload.name.strip() if isinstance(payload.name, str) and payload.name.strip() else payload.name
    profile.session_context = context
    db.commit()
    db.refresh(profile)
    return SessionProfileResponse(
        session_id=profile.session_id,
        name=profile.name,
        session_context=profile.session_context,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
    )
