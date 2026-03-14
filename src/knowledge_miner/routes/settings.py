from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status

from ..ai_filter import describe_ai_filter_runtime
from ..auth import require_api_key
from ..config import settings
from ..rate_limit import require_rate_limit
from ..schemas import AISettingsResponse, AISettingsUpdateRequest, ProviderSettingsResponse, ProviderSettingsUpdateRequest

router = APIRouter(tags=["settings"])


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


def _build_provider_settings_response() -> ProviderSettingsResponse:
    return ProviderSettingsResponse(
        openalex_search_limit=int(settings.openalex_search_limit),
        brave_search_count=int(settings.brave_search_count),
        brave_require_allowlist=bool(settings.brave_require_allowlist),
    )


@router.get("/v1/settings/ai-filter", response_model=AISettingsResponse)
def get_ai_filter_settings(
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> AISettingsResponse:
    return _build_ai_settings_response()


@router.post("/v1/settings/ai-filter", response_model=AISettingsResponse)
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


@router.get("/v1/settings/providers", response_model=ProviderSettingsResponse)
def get_provider_settings(
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> ProviderSettingsResponse:
    return _build_provider_settings_response()


@router.post("/v1/settings/providers", response_model=ProviderSettingsResponse)
def update_provider_settings(
    payload: ProviderSettingsUpdateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
) -> ProviderSettingsResponse:
    provided = payload.model_fields_set
    if "openalex_search_limit" in provided and payload.openalex_search_limit is not None:
        object.__setattr__(settings, "openalex_search_limit", int(payload.openalex_search_limit))
    if "brave_search_count" in provided and payload.brave_search_count is not None:
        object.__setattr__(settings, "brave_search_count", int(payload.brave_search_count))
    if "brave_require_allowlist" in provided and payload.brave_require_allowlist is not None:
        object.__setattr__(settings, "brave_require_allowlist", bool(payload.brave_require_allowlist))
    return _build_provider_settings_response()
