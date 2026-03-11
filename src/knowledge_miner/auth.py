from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import settings


def require_api_key(authorization: str | None = Header(default=None)) -> str:
    if not settings.auth_enabled:
        return "auth_disabled"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_or_invalid_authorization")

    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return token
