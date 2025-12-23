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
    """
    Remove ALL routes registered at 'path' (any methods), so we can re-add
    our canonical handler and avoid shadowing bugs from duplicate registrations.
    """
    kept = []
    for r in app.router.routes:
        if isinstance(r, APIRoute) and r.path == path:
            continue
        kept.append(r)
    app.router.routes = kept


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
        import api.auth as auth  # must exist in repo
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
        # Tenant rules always enforced if tenant header present
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

    # Routers first (in case any of them define /health or /status, we nuke them afterward)
    app.include_router(defend_router)                 # /defend
    app.include_router(defend_router, prefix="/v1")   # /v1/defend
    app.include_router(feed_router)                   # /feed/live

    # Dedupe the known-colliding paths
    _drop_path(app, "/health")
    _drop_path(app, "/status")
    _drop_path(app, "/v1/status")

    @app.get("/health")
    async def health(req: Request) -> dict:
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

    return app


# Module-level default app (tests should call build_app(), but this supports runtime)
app = build_app(auth_enabled=_truthy(os.getenv("FG_AUTH_ENABLED"), default=True))
PY

echo "[*] Compile..."
python -m py_compile api/main.py

echo "[*] Print registered /health routes to prove only one exists..."
python - <<'PY'
from api.main import build_app
from fastapi.routing import APIRoute

app = build_app(False)
hits = []
for r in app.router.routes:
    if isinstance(r, APIRoute) and r.path == "/health":
        hits.append((r.path, sorted(list(r.methods or [])), f"{r.endpoint.__module__}.{r.endpoint.__name__}"))
print("health routes:", hits)
PY

echo "[*] Run the failing test..."
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q
