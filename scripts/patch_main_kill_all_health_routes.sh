#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
echo "[*] Patch timestamp: $TS"
test -f api/main.py && cp -a api/main.py "api/main.py.bak.${TS}" || true

cat > api/main.py <<'PY'
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from api.defend import router as defend_router
from api.feed import router as feed_router


def _truthy(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "on")


def _drop_path(app: FastAPI, path: str) -> None:
    """
    Remove ALL routes registered at `path`, regardless of route class.
    (FastAPI uses APIRoute, but Starlette Route/Mount can also exist.)
    """
    kept = []
    for r in app.router.routes:
        r_path = getattr(r, "path", None)
        if r_path == path:
            continue
        kept.append(r)
    app.router.routes = kept


def _get_header(req: Request, name: str) -> Optional[str]:
    return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())


def _fail() -> None:
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    # STRICT: only literal True enables auth
    frozen = True if auth_enabled is True else False

    app.state._auth_enabled_frozen = frozen
    app.state.auth_enabled = frozen
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")
    app.state.health_instance_id = str(uuid.uuid4())

    @app.middleware("http")
    async def _freeze_auth_state(request: Request, call_next):
        request.app.state.auth_enabled = bool(getattr(request.app.state, "_auth_enabled_frozen", False))
        return await call_next(request)

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

    # Kill collisions, then add canonical endpoints
    _drop_path(app, "/health")
    _drop_path(app, "/status")
    _drop_path(app, "/v1/status")

    @app.get("/health")
    async def health(request: Request) -> dict:
        return {
            "status": "ok",
            "service": request.app.state.service,
            "env": request.app.state.env,
            "auth_enabled": bool(request.app.state.auth_enabled),
            # debug fingerprint so we can prove WHICH handler answered
            "health_instance_id": request.app.state.health_instance_id,
        }

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# default app instance for `from api.main import app`
app = build_app(auth_enabled=_truthy(os.getenv("FG_AUTH_ENABLED"), default=True))
PY

echo "[*] Compile..."
python -m py_compile api/main.py

echo "[*] Show all /health routes in build_app(False) (must be exactly one):"
python - <<'PY'
from api.main import build_app
a = build_app(False)
hits = []
for r in a.router.routes:
    if getattr(r, "path", None) == "/health":
        hits.append((type(r).__name__, getattr(r, "methods", None), getattr(getattr(r, "endpoint", None), "__name__", None)))
print(hits)
PY

echo "[*] Run the exact failing test:"
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q
