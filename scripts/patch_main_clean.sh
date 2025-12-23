#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p scripts

echo "[*] Backup api/main.py -> api/main.py.bak.${TS}"
test -f api/main.py && cp -a api/main.py "api/main.py.bak.${TS}" || true

echo "[*] Writing clean api/main.py..."
python - <<'PY'
from pathlib import Path

code = r'''from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from api.defend import router as defend_router
from api.feed import router as feed_router


def _truthy(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "on")


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    # Freeze config per-app instance (tests rely on this)
    app.state.auth_enabled = bool(auth_enabled)
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")

    def _get_header(req: Request, name: str) -> Optional[str]:
        return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())

    def _fail() -> None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    def _check_tenant_if_present(req: Request) -> None:
        """
        Tenant auth is enforced if x-tenant-id is provided,
        EVEN if auth_enabled=False (revoked tenants must be rejected).
        """
        tenant_id = _get_header(req, "x-tenant-id")
        if not tenant_id:
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        # tests monkeypatch api.auth.get_tenant
        try:
            import api.auth as auth
        except Exception:
            _fail()

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
        # Tenant path always enforced if tenant header present
        _check_tenant_if_present(req)

        # Global auth only if enabled
        if not bool(req.app.state.auth_enabled):
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    @app.get("/health")
    async def health(req: Request) -> dict:
        # IMPORTANT: reflect THIS app instance, not module-level globals
        return {
            "status": "ok",
            "service": req.app.state.service,
            "env": req.app.state.env,
            "auth_enabled": bool(req.app.state.auth_enabled),
        }

    @app.get("/status")
    async def status(req: Request, _: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": req.app.state.service, "env": req.app.state.env}

    @app.get("/v1/status")
    async def v1_status(req: Request, _: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": req.app.state.service, "env": req.app.state.env}

    # Routers
    app.include_router(defend_router)                 # /defend
    app.include_router(defend_router, prefix="/v1")   # /v1/defend
    app.include_router(feed_router)                   # /feed/live

    return app


# Module-level default app (fine, tests should call build_app())
app = build_app(auth_enabled=_truthy(os.getenv("FG_AUTH_ENABLED"), default=True))
'''

Path("api/main.py").write_text(code, encoding="utf-8")
print("Wrote api/main.py (bytes):", len(code))
PY

echo "[*] Sanity compile..."
python -m py_compile api/main.py

echo "[*] Prove build_app(False) actually sets state..."
python - <<'PY'
from api.main import build_app
a = build_app(False)
b = build_app(True)
print("build_app(False).state.auth_enabled =", a.state.auth_enabled)
print("build_app(True).state.auth_enabled  =", b.state.auth_enabled)
PY

echo "[*] Run the failing test..."
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q
