from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..config import settings
from ..db import get_db
from ..models import AcquisitionItem, Run, Source
from ..rate_limit import require_rate_limit
from ..schemas import HMIEventsIngestRequest, HMIEventsIngestResponse

router = APIRouter(tags=["hmi"])
logger_name = "knowledge_miner"
HMI_DIR = Path(__file__).resolve().parents[1] / "hmi"


def _load_hmi_partial(name: str) -> str:
    return (HMI_DIR / "partials" / name).read_text(encoding="utf-8")


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
def hmi_shell(db: Session = Depends(get_db)) -> HTMLResponse:
    run_count = db.scalar(select(func.count()).select_from(Run)) or 0
    if run_count == 0:
        launch_section = "build"
    else:
        review_count = db.scalar(
            select(func.count()).select_from(Source).where(Source.review_status == "needs_review")
        ) or 0
        if review_count > 0:
            launch_section = "review"
        else:
            failed_docs_count = db.scalar(
                select(func.count())
                .select_from(AcquisitionItem)
                .where(AcquisitionItem.status.in_(("failed", "partial")))
            ) or 0
            launch_section = "documents" if failed_docs_count > 0 else "build"
    template = (HMI_DIR / "index.html").read_text(encoding="utf-8")
    token_json = json.dumps(settings.hmi_api_token) if settings.auth_enabled and settings.hmi_api_token else "null"
    auth_enabled_json = "true" if settings.auth_enabled else "false"
    launch_section_json = json.dumps(launch_section)
    static_version = str(
        max(
            int((HMI_DIR / "static" / "hmi.js").stat().st_mtime),
            int((HMI_DIR / "static" / "hmi.css").stat().st_mtime),
        )
    )
    html = (
        template
        .replace("__HMI_DEFAULT_TOKEN_JSON__", token_json)
        .replace("__HMI_AUTH_ENABLED__", auth_enabled_json)
        .replace("__HMI_LAUNCH_SECTION_JSON__", launch_section_json)
        .replace("__HMI_STATIC_VERSION__", static_version)
        .replace("__PARTIAL_CONTROLS__", _load_hmi_partial("controls.html"))
        .replace("__PARTIAL_NAV__", _load_hmi_partial("nav.html"))
        .replace("__PARTIAL_STATUS_STRIP__", _load_hmi_partial("status_strip.html"))
        .replace("__PARTIAL_REVIEW__", _load_hmi_partial("review.html"))
        .replace("__PARTIAL_DOCUMENTS__", _load_hmi_partial("documents.html"))
        .replace("__PARTIAL_LIBRARY__", _load_hmi_partial("library.html"))
        .replace("__PARTIAL_ADVANCED__", _load_hmi_partial("advanced.html"))
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
