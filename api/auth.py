# api/auth.py
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session
import api.auth_scopes as auth_scopes
from api.db import get_db

try:
    from tools.tenants.registry import get_tenant as _registry_get_tenant
except Exception:  # pragma: no cover
    _registry_get_tenant = None


def get_tenant(tenant_id: str):
    if _registry_get_tenant is None:
        return None
    return _registry_get_tenant(tenant_id)


API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)


def _get_expected_api_key() -> str:
    return os.getenv("FG_API_KEY", "supersecret")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def auth_enabled() -> bool:
    if os.getenv("FG_AUTH_ENABLED") is not None:
        return _env_bool("FG_AUTH_ENABLED", default=False)
    return bool(os.getenv("FG_API_KEY"))


def _ui_cookie_name() -> str:
    return os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")


def _extract_key(request: Request, x_api_key: Optional[str]) -> Optional[str]:
    # Header wins
    if x_api_key and str(x_api_key).strip():
        return str(x_api_key).strip()

    # Cookie fallback (HttpOnly cookie, browser sends it; JS cannot read it)
    ck = request.cookies.get(_ui_cookie_name())
    if ck and str(ck).strip():
        return str(ck).strip()

    return None


async def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Depends(API_KEY_HEADER),
    db: Session = Depends(get_db),
) -> None:
    if not auth_enabled():
        return

    raw = _extract_key(request, x_api_key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key"
        )

    expected = _get_expected_api_key()

    # Env key fast path
    if expected and raw == expected:
        return

    # DB-backed key path
    try:
        auth_scopes.verify_api_key_raw(raw, required_scopes=None)
        return
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key"
        )


def require_status_auth(
    _: Request,
    __: None = Depends(verify_api_key),
) -> None:
    return
