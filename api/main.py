from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request

from api.db import init_db
from api.defend import router as defend_router
from api.feed import router as feed_router
from api.stats import router as stats_router
from api.decisions import router as decisions_router
from api.ui import router as ui_router
from api.dev_events import router as dev_events_router

log = logging.getLogger("frostgate")

ERR_INVALID = "Invalid or missing API key"


def _hdr(req: Request, name: str) -> Optional[str]:
    return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())


def _fail(detail: str = ERR_INVALID) -> None:
    raise HTTPException(status_code=401, detail=detail)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_auth_enabled_from_env() -> bool:
    # If explicitly set, that's truth. Else: presence of FG_API_KEY enables auth.
    if os.getenv("FG_AUTH_ENABLED") is not None:
        return _env_bool("FG_AUTH_ENABLED", default=False)
    return bool(os.getenv("FG_API_KEY"))


def _resolve_auth_override(auth_enabled: Optional[bool]) -> bool:
    """Explicit arg wins, always.
    Env is only used when auth_enabled is None.
    """
    if auth_enabled is None:
        return _resolve_auth_enabled_from_env()
    return bool(auth_enabled)


def _resolve_sqlite_path() -> Path:
    p = os.getenv("FG_SQLITE_PATH", "").strip()
    if p:
        return Path(p)

    state_dir = os.getenv("FG_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir) / "frostgate.db"

    # Local-dev sane default
    return Path("artifacts") / "frostgate.db"


def _sanitize_db_url(db_url: str) -> str:
    try:
        u = urlparse(db_url)
        scheme = (u.scheme or "db").split("+", 1)[0]
        host = u.hostname or ""
        port = f":{u.port}" if u.port else ""
        dbname = (u.path or "").lstrip("/")
        if host or dbname:
            return f"{scheme}://{host}{port}/{dbname}"
        return f"{scheme}://(unresolved)"
    except Exception:
        return "db_url:unparseable"


def _global_expected_api_key() -> str:
    return os.getenv("FG_API_KEY") or "supersecret"


def _dev_enabled() -> bool:
    return os.getenv("FG_DEV_EVENTS_ENABLED", "0").strip() == "1"


def build_app(auth_enabled: Optional[bool] = None) -> FastAPI:
    # Explicit param wins. Env only used when param is None.
    resolved_auth_enabled = (
        _resolve_auth_enabled_from_env() if auth_enabled is None else bool(auth_enabled)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # If using sqlite mode, ensure directory exists BEFORE init_db()
            if not os.getenv("FG_DB_URL"):
                p = _resolve_sqlite_path()
                p.parent.mkdir(parents=True, exist_ok=True)

            init_db()
            app.state.db_init_ok = True
            app.state.db_init_error = None
        except Exception as e:
            app.state.db_init_ok = False
            app.state.db_init_error = f"{type(e).__name__}: {e}"
            log.exception("DB init failed")
        yield

    app = FastAPI(title="frostgate-core", version="0.1.0", lifespan=lifespan)
    # auth_enabled precedence: explicit param wins; else env/default logic inside module
    app.state.auth_enabled = resolved_auth_enabled


    # Frozen state
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")
    app.state.app_instance_id = str(uuid.uuid4())
    app.state.db_init_ok = False
    app.state.db_init_error = None

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
        # Tenant auth always enforced if present
        check_tenant_if_present(req)

        # Global auth gate
        if not bool(app.state.auth_enabled):
            return

        api_key = _hdr(req, "X-API-Key")
        if not api_key:
            _fail()

        if str(api_key) != str(_global_expected_api_key()):
            _fail()

    # Routes (no duplicates, no shadowing)
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)
    app.include_router(decisions_router)
    app.include_router(stats_router)
    app.include_router(ui_router)

    # Dev-only routes are only mounted when explicitly enabled
    if _dev_enabled():
        app.include_router(dev_events_router)

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
        if not bool(app.state.db_init_ok):
            raise HTTPException(status_code=503, detail=f"db_init_failed: {app.state.db_init_error or 'unknown'}")

        if os.getenv("FG_DB_URL"):
            return {"status": "ready", "db": "url"}

        p = _resolve_sqlite_path()
        # At this point, init_db should have created it if sqlite is configured correctly.
        if not p.exists():
            raise HTTPException(status_code=503, detail=f"DB missing: {p}")

        return {"status": "ready", "db": "sqlite", "path": str(p)}

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/stats/debug")
    async def stats_debug(_: None = Depends(require_status_auth)) -> dict:
        db_url = os.getenv("FG_DB_URL")
        result: dict = {
            "service": app.state.service,
            "env": app.state.env,
            "app_instance_id": app.state.app_instance_id,
            "auth_enabled": bool(app.state.auth_enabled),
            "db_mode": "url" if db_url else "sqlite",
            "db_init_ok": bool(app.state.db_init_ok),
            "db_init_error": app.state.db_init_error,
            "fg_state_dir": os.getenv("FG_STATE_DIR"),
            "fg_sqlite_path_env": os.getenv("FG_SQLITE_PATH"),
        }

        if db_url:
            result["stats_source_db"] = _sanitize_db_url(db_url)
            result["stats_source_db_size_bytes"] = None
            return result

        try:
            p = _resolve_sqlite_path()
            exists = p.exists()
            size = p.stat().st_size if exists else 0
            result["sqlite_path_resolved"] = str(p)
            result["sqlite_exists"] = exists
            result["sqlite_size_bytes"] = size
            result["stats_source_db"] = f"sqlite:{p}"
            result["stats_source_db_size_bytes"] = size
        except Exception as e:
            result["sqlite_path_resolved_error"] = f"{type(e).__name__}: {e}"
            result["stats_source_db"] = "sqlite:unresolved"
            result["stats_source_db_size_bytes"] = 0

        return result

    return app


app = build_app()
