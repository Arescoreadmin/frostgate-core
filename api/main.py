from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from api.defend import router as defend_router
from api.feed import router as feed_router


def build_app(auth_enabled: bool = True) -> FastAPI:
    """
    Test-facing app factory.

    Guarantees:
      - /health reflects build_app(auth_enabled)
      - /status & /v1/status require x-api-key when auth_enabled=True
      - revoked tenants rejected when x-tenant-id present (even if auth disabled)
    """
    app = FastAPI(title="frostgate-core", version="0.1.0")
    app.state.auth_enabled = bool(auth_enabled)
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

        if not app.state.auth_enabled:
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # Define /health BEFORE routers so nothing can shadow it.
    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": app.state.service,
            "env": app.state.env,
            "auth_enabled": bool(app.state.auth_enabled),
        }

    # Routers
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# default import-time instance (some code/tests may import api.main.app)
def _env_auth_enabled() -> bool:
    raw = os.getenv("FG_AUTH_ENABLED")
    if raw is not None:
        return raw in ("1", "true", "True", "yes", "on")
    return bool(os.getenv("FG_API_KEY"))


app = build_app(auth_enabled=_env_auth_enabled())
