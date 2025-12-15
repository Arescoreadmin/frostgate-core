from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, Header, status
from fastapi.security import APIKeyHeader

# ---------- Optional tenant registry hook ----------

try:
    # Optional tenant registry hook; tests can monkeypatch get_tenant()
    from tools.tenants.registry import get_tenant as _registry_get_tenant
except Exception:  # pragma: no cover
    _registry_get_tenant = None


def get_tenant(tenant_id: str):
    """
    Lightweight shim so tests/other modules can monkeypatch api.auth.get_tenant.

    In real usage, if tools.tenants.registry exists, delegate there.
    Otherwise returns None (no tenant found).
    """
    if _registry_get_tenant is None:
        return None
    return _registry_get_tenant(tenant_id)


# ---------- Global API key (FG_API_KEY) ----------

# Header name we expect from callers (e.g., edge gateways, Spear)
API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)


def _get_expected_api_key() -> str:
    """
    Expected API key for Frostgate core.

    In dev, default to 'supersecret' so you can hammer it with curl
    without wiring extra env. In prod, FG_API_KEY **must** be set.
    """
    return os.getenv("FG_API_KEY", "supersecret")


async def verify_api_key(api_key: Optional[str] = Depends(API_KEY_HEADER)) -> None:
    """
    FastAPI dependency that enforces the x-api-key header.

    Used in api.main:

        dependencies=[Depends(verify_api_key)]
    """
    expected = _get_expected_api_key()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    if api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    # presence + match == OK


# ---------- Tenant-aware guard (optional) ----------

async def tenant_guard(
    x_tenant_id: Optional[str] = Header(default=None, alias="x-tenant-id"),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> None:
    """
    Tenant-aware guard layered *on top of* global API key.

    Behavior:

      - If NO x-tenant-id:
          - Do nothing. Global API key still enforced by verify_api_key.
      - If x-tenant-id IS present:
          - x-api-key is required.
          - Tenant must exist (get_tenant).
          - tenant.status must be "active".
          - tenant.api_key must match x-api-key.
    """
    # No tenant scope => allow through, rely on global key behavior.
    if not x_tenant_id:
        return

    # Tenant path: require tenant API key header
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing tenant auth headers")

    tenant = get_tenant(x_tenant_id)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unknown tenant")

    status_val = getattr(tenant, "status", "active")
    if status_val != "active":
        # Covers "revoked" and any other non-active states
        raise HTTPException(status_code=401, detail="Tenant not active")

    tenant_key = getattr(tenant, "api_key", None)
    if not tenant_key or tenant_key != x_api_key:
        raise HTTPException(status_code=401, detail="Invalid tenant API key")

    # Valid tenant, active, key matches
    return


# ---------- Legacy / stub verify_tenant ----------

async def verify_tenant(request: Request) -> None:
    """
    Legacy tenant guard stub.

    For now, we just ensure a tenant identifier is present *somewhere*
    (body or headers). You can delete this once everything is on tenant_guard().
    """
    # Try headers first
    tenant_header = (
        request.headers.get("x-tenant-id")
        or request.headers.get("x-tenant")
        or request.headers.get("tenant-id")
    )

    if tenant_header:
        return

    # Fallback: try JSON body with `tenant_id`
    try:
        body = await request.json()
    except Exception:
        body = {}

    tenant_body = body.get("tenant_id") if isinstance(body, dict) else None

    if tenant_body:
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing tenant identifier",
    )
