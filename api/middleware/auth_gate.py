from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


@dataclass(frozen=True)
class AuthGateConfig:
    public_paths: tuple[str, ...] = ("/health", "/health/ready", "/ui/token")
    header_authgate: str = "x-fg-authgate"
    header_gate: str = "x-fg-gate"
    header_path: str = "x-fg-path"


class AuthGateMiddleware(BaseHTTPMiddleware):
    """
    Auth gate that NEVER raises from middleware. It always returns a Response on failure.
    """
    def __init__(
        self,
        app,
        require_status_auth: Callable[[Request], None],
        config: Optional[AuthGateConfig] = None,
    ):
        super().__init__(app)
        self.require_status_auth = require_status_auth
        self.config = config or AuthGateConfig()

    def _stamp(self, resp: Response, request: Request, gate: str) -> Response:
        resp.headers[self.config.header_authgate] = "1"
        resp.headers[self.config.header_gate] = gate
        resp.headers[self.config.header_path] = request.url.path
        return resp

    def _is_public(self, path: str) -> bool:
        # exact match only; if you want prefix logic later, do it intentionally
        return path in self.config.public_paths

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if self._is_public(path):
            resp = await call_next(request)
            return self._stamp(resp, request, "public")

        try:
            self.require_status_auth(request)
        except HTTPException as e:
            # IMPORTANT: middleware returns, never raises
            resp = JSONResponse(
                {"detail": getattr(e, "detail", str(e)), "auth": "blocked"},
                status_code=int(getattr(e, "status_code", 401) or 401),
            )
            return self._stamp(resp, request, "blocked")
        except Exception as e:
            # hard shield: don't leak stack traces to clients
            resp = JSONResponse(
                {"detail": "Auth gate error", "auth": "blocked"},
                status_code=500,
            )
            return self._stamp(resp, request, "blocked")

        resp = await call_next(request)
        return self._stamp(resp, request, "protected")
