# api/auth.py
from __future__ import annotations

import os
from typing import Optional, Any, Dict

from fastapi import Depends, HTTPException, Request, Header, status
from api.auth_scopes import verify_api_key_raw
from sqlalchemy.orm import Session
from api.db import get_db
from fastapi.security import APIKeyHeader

# ---------- Optional tenant registry hook ----------

try:
    from tools.tenants.registry import get_tenant as _registry_get_tenant
except Exception:  # pragma: no cover
    _registry_get_tenant = None


def get_tenant(tenant_id: str):
    if _registry_get_tenant is None:
        return None
    return _registry_get_tenant(tenant_id)


# ---------- Global API key (FG_API_KEY) ----------

API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)


def _get_expected_api_key() -> str:
    return os.getenv("FG_API_KEY", "supersecret")


async def verify_api_key(
    x_api_key: str | None = None,
    db: Session = Depends(get_db),
) -> None:
    """
    Accept either:
      - legacy env key (FG_API_KEY / whatever _get_expected_api_key reads)
      - DB-backed API key (ApiKey.key_hash == hash_api_key(raw))
    """
    expected = _get_expected_api_key()

    if x_api_key is None or not str(x_api_key).strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    raw = str(x_api_key).strip()

    # 1) env legacy path
    if expected and raw == expected:
        return

    # 2) DB-backed path
    try:
        verify_api_key_raw(raw_key=raw, db=db, required_scopes=None)
        return
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

