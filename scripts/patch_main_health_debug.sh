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


def _get_header(req: Request, name: str) -> Optional[str]:
    return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())


def _fail() -> None:
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    # Build-time fingerprinting (debug the insanity)
    build_arg = auth_enabled
    build_arg_type = type(auth_enabled).__name__
    frozen = True if auth_enabled is True else False

    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")
    app.state.health_instance_id = str(uuid.uuid4())

    # freeze + expose
    app.state._build_auth_arg = build_arg
    app.state._build_auth_arg_type = build_arg_type
    app.state._auth_enabled_frozen = frozen

    # this is mutable, but we’ll slam it each request
    app.state.auth_enabled = frozen

    @app.middleware("http")
    async def _freeze_auth_state(request: Request, call_next):
        request.app.state.auth_enabled = bool(request.app.state._auth_enabled_frozen)
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

        if not bool(req.app.state._auth_enabled_frozen):
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # routers
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)

    @app.get("/health")
    async def health(request: Request) -> dict:
        return {
            "status": "ok",
            "service": request.app.state.service,
            "env": request.app.state.env,
            # what tests should match
            "auth_enabled": bool(request.app.state._auth_enabled_frozen),
            # debug: prove what build_app received
            "build_auth_arg": request.app.state._build_auth_arg,
            "build_auth_arg_type": request.app.state._build_auth_arg_type,
            # debug: prove runtime state
            "auth_state": bool(request.app.state.auth_enabled),
            "health_instance_id": request.app.state.health_instance_id,
        }

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# default app instance
app = build_app(auth_enabled=_truthy(os.getenv("FG_AUTH_ENABLED"), default=True))
PY

echo "[*] Compile..."
python -m py_compile api/main.py

echo "[*] Run the failing test and show /health payload:"
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q || true

echo
echo "[*] Now manually inspect /health for build_app(False):"
python - <<'PY'
from api.main import build_app
from starlette.testclient import TestClient

a = build_app(False)
c = TestClient(a)
print(c.get("/health").json())
PY
