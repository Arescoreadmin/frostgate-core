from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from api.defend import router as defend_router
from api.feed import router as feed_router
from api.stats import router as stats_router



ERR_INVALID = "Invalid or missing API key"


def _hdr(req: Request, name: str) -> Optional[str]:
    return (
        req.headers.get(name)
        or req.headers.get(name.lower())
        or req.headers.get(name.upper())
    )


def _fail(detail: str = ERR_INVALID) -> None:
    raise HTTPException(status_code=401, detail=detail)


def build_app(auth_enabled: bool = True) -> FastAPI:
    app = FastAPI(title="frostgate-core", version="0.1.0")

    # ---- App state ----
    app.state.auth_enabled = bool(auth_enabled)
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")
    app.state.app_instance_id = str(uuid.uuid4())

    # ---- Auth helpers ----
    def check_tenant_if_present(req: Request) -> None:
        tenant_id = _hdr(req, "X-Tenant-Id")
        if not tenant_id:
            return

        api_key = _hdr(req, "X-API-Key")
        if not api_key:
            _fail()

        import api.auth as auth  # monkeypatched in tests

        get_tenant = getattr(auth, "get_tenant", None)
        if not callable(get_tenant):
            _fail()

        tenant = get_tenant(str(tenant_id))
        if tenant is None:
            _fail()

        status = getattr(tenant, "status", None)
        if status and str(status).lower() != "active":
            _fail("Tenant revoked")

        expected = getattr(tenant, "api_key", None)
        if expected is None or str(expected) != str(api_key):
            _fail()

    def require_status_auth(req: Request) -> None:
        check_tenant_if_present(req)

        if not app.state.auth_enabled:
            return

        api_key = _hdr(req, "X-API-Key")
        if not api_key:
            _fail()

        expected = os.getenv("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # ---- Routers ----
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)
    app.include_router(stats_router)

    # ---- Health ----
    @app.get("/health")
    async def health(request: Request) -> dict:
        return {
            "status": "ok",
            "service": request.app.state.service,
            "env": request.app.state.env,
            "auth_enabled": bool(request.app.state.auth_enabled),
            "app_instance_id": request.app.state.app_instance_id,
        }

    @app.get("/health/live")
    async def health_live() -> dict:
        return {"status": "live"}

    @app.get("/health/ready")
    async def health_ready() -> dict:
        return {"status": "ready"}

    # ---- Status ----
    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# Import-time default app (smoke tests depend on this)
app = build_app(
    auth_enabled=bool(os.getenv("FG_API_KEY"))
)
