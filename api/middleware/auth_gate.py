from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


@dataclass(frozen=True)
class AuthGateConfig:
    public_paths: tuple[str, ...] = ("/health", "/health/ready", "/ui", "/ui/token")
    header_authgate: str = "x-fg-authgate"
    header_gate: str = "x-fg-gate"
    header_path: str = "x-fg-path"


def _auth_enabled() -> bool:
    v = (os.getenv("FG_AUTH_ENABLED", "1") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _is_public(path: str, config: AuthGateConfig) -> bool:
    for p in config.public_paths:
        if path == p or path.startswith(p.rstrip("/") + "/"):
            return True
    return False


class AuthGateMiddleware(BaseHTTPMiddleware):
    """
    Middleware MUST be dumb:
      - decide public/protected
      - validate key via auth_scopes.verify_api_key_raw
    Anything else belongs in dependencies, not middleware.
    """

    def __init__(
        self,
        app,
        require_status_auth: Callable[
            [Request], None
        ],  # kept for main.py compatibility, ignored on purpose
        config: Optional[AuthGateConfig] = None,
    ):
        super().__init__(app)
        self._ignored_require_status_auth = require_status_auth
        self.config = config or AuthGateConfig()

    def _stamp(self, resp: Response, request: Request, gate: str) -> Response:
        resp.headers[self.config.header_authgate] = "1"
        resp.headers[self.config.header_gate] = gate
        resp.headers[self.config.header_path] = request.url.path
        return resp

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not _auth_enabled():
            resp = await call_next(request)
            return self._stamp(resp, request, "auth_disabled")

        if _is_public(path, self.config):
            resp = await call_next(request)
            return self._stamp(resp, request, "public")

        # Prefer header; fallback to UI cookie if header is missing/blank
        raw = (request.headers.get("x-api-key") or "").strip()
        ck_name = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")
        if not raw:
            raw = (request.cookies.get(ck_name) or "").strip()

        if not raw:
            resp = JSONResponse(
                {"detail": "Invalid or missing API key", "auth": "blocked"},
                status_code=401,
            )
            return self._stamp(resp, request, "blocked")

        from api.auth_scopes import verify_api_key_raw

        if not verify_api_key_raw(raw, required_scopes=None):
            resp = JSONResponse(
                {"detail": "Invalid or missing API key", "auth": "blocked"},
                status_code=401,
            )
            return self._stamp(resp, request, "blocked")

        resp = await call_next(request)
        return self._stamp(resp, request, "protected")
