from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from api.db import init_db, _resolve_sqlite_path
from api.decisions import router as decisions_router
from api.defend import router as defend_router
from api.dev_events import router as dev_events_router
from api.feed import router as feed_router
from api.stats import router as stats_router
from api.ui import router as ui_router

# Optional "spine" modules (feature-flag gated, fail-open)
try:
    from api.forensics import forensics_enabled, router as forensics_router
except Exception:  # pragma: no cover

    def forensics_enabled() -> bool:  # type: ignore
        return False

    forensics_router = None  # type: ignore

try:
    from api.governance import governance_enabled, router as governance_router
except Exception:  # pragma: no cover

    def governance_enabled() -> bool:  # type: ignore
        return False

    governance_router = None  # type: ignore

try:
    # Mission envelope module: accept either exported name, but standardize on mission_envelope_enabled()
    from api.mission_envelope import router as mission_router

    try:
        from api.mission_envelope import mission_envelope_enabled  # preferred
    except Exception:  # pragma: no cover
        from api.mission_envelope import (
            mission_envelopes_enabled as mission_envelope_enabled,
        )  # type: ignore
except Exception:  # pragma: no cover

    def mission_envelope_enabled() -> bool:  # type: ignore
        return False

    mission_router = None  # type: ignore

try:
    from api.ring_router import ring_router_enabled, router as ring_router
except Exception:  # pragma: no cover

    def ring_router_enabled() -> bool:  # type: ignore
        return False

    ring_router = None  # type: ignore

try:
    from api.roe_engine import roe_engine_enabled, router as roe_router
except Exception:  # pragma: no cover

    def roe_engine_enabled() -> bool:  # type: ignore
        return False

    roe_router = None  # type: ignore

from api.middleware.auth_gate import AuthGateMiddleware, AuthGateConfig


log = logging.getLogger("frostgate")

ERR_INVALID = "Invalid or missing API key"
UI_COOKIE_NAME = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_auth_enabled_from_env() -> bool:
    # Explicit flag wins. Else: presence of FG_API_KEY implies auth enabled.
    if os.getenv("FG_AUTH_ENABLED") is not None:
        return _env_bool("FG_AUTH_ENABLED", default=False)
    return bool((os.getenv("FG_API_KEY") or "").strip())


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
    return (os.getenv("FG_DEV_EVENTS_ENABLED") or "0").strip() == "1"


class FGExceptionShieldMiddleware:
    """
    ASGI middleware that converts HTTPException (and ExceptionGroup containing one)
    into a clean JSON response instead of a 500.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        except HTTPException as e:
            resp = JSONResponse(
                status_code=e.status_code,
                content={"detail": getattr(e, "detail", str(e))},
            )
            await resp(scope, receive, send)
        except ExceptionGroup as eg:  # py3.11+
            http_exc = None
            for ex in eg.exceptions:
                if isinstance(ex, HTTPException):
                    http_exc = ex
                    break
            if http_exc is not None:
                resp = JSONResponse(
                    status_code=http_exc.status_code,
                    content={"detail": getattr(http_exc, "detail", str(http_exc))},
                )
                await resp(scope, receive, send)
            else:
                raise


def build_app(auth_enabled: Optional[bool] = None) -> FastAPI:
    resolved_auth_enabled = (
        _resolve_auth_enabled_from_env() if auth_enabled is None else bool(auth_enabled)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # sqlite mode: ensure dir exists BEFORE init_db()
            if not (os.getenv("FG_DB_URL") or "").strip():
                p = _resolve_sqlite_path()
                Path(p).parent.mkdir(parents=True, exist_ok=True)

            init_db()
            app.state.db_init_ok = True
            app.state.db_init_error = None
        except Exception as e:
            app.state.db_init_ok = False
            app.state.db_init_error = f"{type(e).__name__}: {e}"
            log.exception("DB init failed")
        yield

    app = FastAPI(title="frostgate-core", version="0.1.0", lifespan=lifespan)

    # Shield first (outermost)
    app.add_middleware(FGExceptionShieldMiddleware)

    # Frozen state
    app.state.auth_enabled = bool(resolved_auth_enabled)
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")
    app.state.app_instance_id = str(uuid.uuid4())
    app.state.db_init_ok = False
    app.state.db_init_error = None

    def _fail(detail: str = ERR_INVALID) -> None:
        raise HTTPException(status_code=401, detail=detail)

    def _hdr(req: Request, name: str) -> Optional[str]:
        v = req.headers.get(name)  # headers are case-insensitive
        v = str(v).strip() if v is not None else ""
        return v or None

    def check_tenant_if_present(req: Request) -> None:
        """
        Optional tenant auth:
        - If X-Tenant-Id is present, enforce tenant key validation even if global auth is disabled.
        - Fail closed if tenant registry hook isn't available.
        """
        tenant_id = _hdr(req, "X-Tenant-Id")
        if not tenant_id:
            return

        api_key = _hdr(req, "X-API-Key")
        if not api_key:
            ck = req.cookies.get(UI_COOKIE_NAME)
            api_key = str(ck).strip() if ck and str(ck).strip() else None

        if not api_key:
            _fail()

        try:
            import api.auth as auth_mod  # noqa: E402
        except Exception:
            _fail()

        get_tenant = getattr(auth_mod, "get_tenant", None)
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
            ck = req.cookies.get(UI_COOKIE_NAME)
            api_key = str(ck).strip() if ck and str(ck).strip() else None

        if not api_key:
            _fail()

        if str(api_key) != str(_global_expected_api_key()):
            _fail()

    # Compatibility shim: older modules importing require_status_auth from api.auth
    try:
        import api.auth as auth_mod  # noqa: E402

        if not hasattr(auth_mod, "require_status_auth"):
            setattr(auth_mod, "require_status_auth", require_status_auth)
    except Exception:
        pass

    app.add_middleware(
        AuthGateMiddleware,
        require_status_auth=require_status_auth,
        config=AuthGateConfig(
            public_paths=(
                "/health",
                "/health/live",
                "/health/ready",
                "/ui",
                "/ui/token",
                "/openapi.json",
                "/docs",
                "/redoc",
            )
        ),
    )

    # ---- Routers ----
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)
    app.include_router(decisions_router)
    app.include_router(stats_router)
    app.include_router(ui_router)
    if mission_router is not None and mission_envelope_enabled():
        app.include_router(mission_router)
    if ring_router is not None and ring_router_enabled():
        app.include_router(ring_router)
    if roe_router is not None and roe_engine_enabled():
        app.include_router(roe_router)
    if forensics_router is not None and forensics_enabled():
        app.include_router(forensics_router)
    if governance_router is not None and governance_enabled():
        app.include_router(governance_router)

    if _dev_enabled():
        app.include_router(dev_events_router)

    # ---- Health / Status ----
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
            raise HTTPException(
                status_code=503,
                detail=f"db_init_failed: {app.state.db_init_error or 'unknown'}",
            )

        if (os.getenv("FG_DB_URL") or "").strip():
            return {"status": "ready", "db": "url"}

        p = Path(_resolve_sqlite_path())
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
        db_url = (os.getenv("FG_DB_URL") or "").strip()
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
            p = Path(_resolve_sqlite_path())
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

    @app.get("/_debug/routes")
    async def debug_routes(request: Request) -> dict:
        try:
            require_status_auth(request)

            out = []
            for r in request.app.router.routes:
                path = getattr(r, "path", None)
                if not path:
                    continue
                endpoint = getattr(r, "endpoint", None)
                mod = getattr(endpoint, "__module__", None) if endpoint else None
                name = getattr(endpoint, "__name__", None) if endpoint else None
                methods = sorted(list(getattr(r, "methods", []) or []))

                out.append(
                    {
                        "path": path,
                        "methods": methods,
                        "endpoint": f"{mod}.{name}" if mod and name else None,
                        "name": getattr(r, "name", None),
                    }
                )

            out.sort(key=lambda x: (x["path"], ",".join(x["methods"])))
            return {"ok": True, "error": None, "routes": out}
        except HTTPException as e:
            return {"ok": False, "error": f"{e.status_code}: {e.detail}", "routes": []}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "routes": []}

    return app


app = build_app()
