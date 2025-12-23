#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
echo "[*] Patch timestamp: $TS"
test -f api/main.py && cp -a api/main.py "api/main.py.bak.${TS}" || true

cat > api/main.py <<'PY'
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from api.defend import router as defend_router
from api.feed import router as feed_router


def _truthy(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "on")


def _drop_path(app: FastAPI, path: str) -> None:
    """Remove ALL routes registered at `path` (any methods)."""
    kept = []
    for r in app.router.routes:
        if isinstance(r, APIRoute) and r.path == path:
            continue
        kept.append(r)
    app.router.routes = kept


def _get_header(req: Request, name: str) -> Optional[str]:
    return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())


def _fail() -> None:
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def health(request: Request) -> dict:
    # IMPORTANT: no closure capture. Read from per-app state only.
    return {
        "status": "ok",
        "service": getattr(request.app.state, "service", "frostgate-core"),
        "env": getattr(request.app.state, "env", "dev"),
        "auth_enabled": bool(getattr(request.app.state, "auth_enabled", False)),
    }


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    # STRICT: only literal True enables auth
    frozen = True if auth_enabled is True else False

    # store both: frozen reference + active state
    app.state._auth_enabled_frozen = frozen
    app.state.auth_enabled = frozen
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")

    # slam auth state back each request so nothing can mutate it
    @app.middleware("http")
    async def _freeze_auth_state(request: Request, call_next):
        request.app.state.auth_enabled = bool(getattr(request.app.state, "_auth_enabled_frozen", False))
        return await call_next(request)

    def _check_tenant_if_present(req: Request) -> None:
        """
        Tenant auth is enforced if x-tenant-id is provided,
        even if auth is globally disabled (revoked tenants must be rejected).
        """
        tenant_id = _get_header(req, "x-tenant-id")
        if not tenant_id:
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        import api.auth as auth  # tests monkeypatch auth.get_tenant
        get_tenant = getattr(auth, "get_tenant", None)
        if not callable(get_tenant):
            _fail()

        tenant = get_tenant(str(tenant_id))
        if tenant is None:
            _fail()

        status = getattr(tenant, "status", None)
        if status and str(status).lower() != "active":
            _fail()

        expected_key = getattr(tenant, "api_key", None)
        if expected_key is None or str(expected_key) != str(api_key):
            _fail()

    def require_status_auth(req: Request) -> None:
        _check_tenant_if_present(req)

        # global auth gate
        if not bool(getattr(req.app.state, "_auth_enabled_frozen", False)):
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # Routers
    app.include_router(defend_router)               # /defend
    app.include_router(defend_router, prefix="/v1") # /v1/defend
    app.include_router(feed_router)                 # /feed/live

    # Remove any previously-registered collisions, then add canonical endpoints.
    _drop_path(app, "/health")
    _drop_path(app, "/status")
    _drop_path(app, "/v1/status")

    app.add_api_route("/health", health, methods=["GET"])

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# Default app instance (imported by tests that do `from api.main import app`)
app = build_app(auth_enabled=_truthy(os.getenv("FG_AUTH_ENABLED"), default=True))
PY

echo "[*] Compile..."
python -m py_compile api/main.py

echo "[*] Prove build_app(False) state + /health are consistent:"
python - <<'PY'
import asyncio
from httpx import AsyncClient
from httpx import ASGITransport
from api.main import build_app

async def main():
    a = build_app(False)
    t = ASGITransport(app=a)
    async with AsyncClient(transport=t, base_url="http://test") as c:
        r = await c.get("/health")
        print("build_app(False).state.auth_enabled =", a.state.auth_enabled)
        print("/health json:", r.json())

asyncio.run(main())
PY

echo "[*] Run failing test..."
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q
