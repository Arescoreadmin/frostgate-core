#!/usr/bin/env bash
set -euo pipefail
TS="$(date +%Y%m%d_%H%M%S)"
cp -a api/main.py "api/main.py.bak.${TS}" || true

cat > api/main.py <<'PY'
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from api.defend import router as defend_router
from api.feed import router as feed_router


def _drop_path(app: FastAPI, path: str) -> None:
    kept = []
    for r in app.router.routes:
        if getattr(r, "path", None) == path:
            continue
        kept.append(r)
    app.router.routes = kept


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    AUTH_ENABLED = True if auth_enabled is True else False

    app.state.auth_enabled = AUTH_ENABLED
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")

    def _get_header(req: Request, name: str) -> Optional[str]:
        return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())

    def _fail() -> None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    def _check_tenant_if_present(req: Request) -> None:
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

        if not AUTH_ENABLED:
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # ---- ensure OUR /health wins ----
    _drop_path(app, "/health")
    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": app.state.service,
            "env": app.state.env,
            "auth_enabled": AUTH_ENABLED,
        }

    # routers AFTER health (so their /health can’t shadow ours)
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)

    _drop_path(app, "/status")
    _drop_path(app, "/v1/status")

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


app = build_app(
    auth_enabled=True
    if os.getenv("FG_AUTH_ENABLED") in (None, "", "1", "true", "True", "yes", "on")
    else False
)
PY

find . -type d -name "__pycache__" -prune -exec rm -rf {} + || true
rm -rf .pytest_cache || true
python -m py_compile api/main.py
