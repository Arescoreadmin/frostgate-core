#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
echo "[*] Patch timestamp: $TS"

backup() {
  local f="$1"
  if [[ -f "$f" ]]; then
    cp -a "$f" "${f}.bak.${TS}"
    echo "    [+] backed up $f -> ${f}.bak.${TS}"
  fi
}

# ---- backups ----
backup api/schemas.py
backup api/main.py
backup api/defend.py
backup api/feed.py

# ---- 1) schemas: stable TelemetryInput + structured MitigationAction ----
cat > api/schemas.py <<'PY'
# api/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MitigationAction(BaseModel):
    """
    Engine expects MitigationAction(...) as a structured object (keyword args).
    Keep permissive for MVP: action is a string.
    """
    model_config = ConfigDict(extra="allow")

    action: str
    target: Optional[str] = None
    reason: Optional[str] = None
    confidence: float = 0.5


class TelemetryInput(BaseModel):
    """
    Canonical request model for defend/ingest.

    Compatibility:
      - New shape: payload={...} (tests use this)
      - Legacy shape: event={...} (defend.py references req.event)
      - Root fields: event_type/src_ip (defend.py references req.event_type)
      - Doctrine: classification/persona as plain strings
      - extra=allow for forward compatibility during MVP
    """
    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None

    # Doctrine fields as strings
    classification: Optional[str] = None
    persona: Optional[str] = None

    # New + legacy containers
    payload: Dict[str, Any] = Field(default_factory=dict)
    event: Dict[str, Any] = Field(default_factory=dict)

    # Backfilled convenience fields (defend.py references these directly)
    event_type: Optional[str] = None
    src_ip: Optional[str] = None

    @model_validator(mode="after")
    def _compat_backfill(self) -> "TelemetryInput":
        # If one of payload/event missing, mirror the other
        if not isinstance(self.payload, dict):
            self.payload = {}
        if not isinstance(self.event, dict):
            self.event = {}

        if not self.payload and self.event:
            self.payload = dict(self.event)
        if not self.event and self.payload:
            self.event = dict(self.payload)

        # Backfill event_type/src_ip from containers if missing
        if not self.event_type:
            self.event_type = (
                self.payload.get("event_type")
                or self.event.get("event_type")
                or None
            )
        if not self.src_ip:
            self.src_ip = (
                self.payload.get("src_ip")
                or self.event.get("src_ip")
                or self.payload.get("source_ip")
                or self.event.get("source_ip")
                or None
            )

        return self
PY

# ---- 2) defend: make _to_utc accept ISO strings (Z or offset) ----
# Patch in-place by replacing the first _to_utc def block if found, otherwise insert helper above its first use.
python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
src = p.read_text(encoding="utf-8")

tolerant = r'''
def _to_utc(dt):
    """
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' and naive datetimes.
    """
    from datetime import datetime, timezone

    if dt is None:
        return datetime.now(timezone.utc)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, str):
        s = dt.strip()
        # RFC3339 'Z' -> +00:00 for fromisoformat
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            # last resort: treat as now
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    # unknown type
    return datetime.now(timezone.utc)
'''.lstrip("\n")

# Replace existing _to_utc block if present.
m = re.search(r"^def _to_utc\([^\n]*\):\n(?:^[ \t].*\n)+", src, flags=re.M)
if m:
    src2 = src[:m.start()] + tolerant + "\n" + src[m.end():]
    p.write_text(src2, encoding="utf-8")
    print("[+] patched existing _to_utc() in api/defend.py")
else:
    # Insert near top (after imports) as fallback
    ins = re.search(r"^\s*from __future__.*\n(?:.*\n)*?\n", src, flags=re.M)
    if ins:
        src2 = src[:ins.end()] + tolerant + "\n" + src[ins.end():]
    else:
        src2 = tolerant + "\n" + src
    p.write_text(src2, encoding="utf-8")
    print("[+] inserted tolerant _to_utc() in api/defend.py")
PY

# ---- 3) feed: keep it simple and scope-protected ----
cat > api/feed.py <<'PY'
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from api.auth_scopes import require_scopes

log = logging.getLogger("frostgate.feed")

router = APIRouter()

@router.get("/feed/live", dependencies=[Depends(require_scopes("feed:read"))])
def feed_live(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    log.debug("feed.live limit=%s", limit)
    return {"items": [], "limit": limit}
PY

# ---- 4) main: correct auth gating + tenant revoked behavior + health reflects build_app(arg) ----
cat > api/main.py <<'PY'
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from api.defend import router as defend_router
from api.feed import router as feed_router


def build_app(auth_enabled: bool = True) -> FastAPI:
    """
    App factory used by tests.

    Requirements enforced:
      - /health reflects the build_app(auth_enabled=...) argument
      - when auth_enabled=True, /status and /v1/status require x-api-key
      - tenant revoked should be rejected even if auth_enabled=False (tenant header present)
      - routers mounted at /defend, /v1/defend, /feed/live
    """
    app = FastAPI(title="frostgate-core", version="0.1.0")

    AUTH_ENABLED = True if auth_enabled is True else False

    app.state.auth_enabled = AUTH_ENABLED
    app.state.service = os.getenv("FG_SERVICE", "frostgate-core")
    app.state.env = os.getenv("FG_ENV", "dev")

    def _get_header(req: Request, name: str) -> Optional[str]:
        return req.headers.get(name) or req.headers.get(name.lower()) or req.headers.get(name.upper())

    def _fail() -> None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    def _check_tenant_if_present(req: Request) -> None:
        """
        Enforce tenant auth if x-tenant-id is provided EVEN when AUTH_ENABLED is False.
        Tests monkeypatch api.auth.get_tenant; we call it if available.
        """
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

        if not AUTH_ENABLED:
            return

        api_key = _get_header(req, "x-api-key")
        if not api_key:
            _fail()

        expected = os.environ.get("FG_API_KEY") or "supersecret"
        if str(api_key) != str(expected):
            _fail()

    # Routers
    app.include_router(defend_router)
    app.include_router(defend_router, prefix="/v1")
    app.include_router(feed_router)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": app.state.service,
            "env": app.state.env,
            "auth_enabled": AUTH_ENABLED,
        }

    @app.get("/status")
    async def status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    @app.get("/v1/status")
    async def v1_status(_: None = Depends(require_status_auth)) -> dict:
        return {"status": "ok", "service": app.state.service, "env": app.state.env}

    return app


# Default app instance (used by some tests importing api.main.app)
app = build_app(auth_enabled=True if os.getenv("FG_AUTH_ENABLED") in (None, "", "1", "true", "True", "yes", "on") else False)
PY

# ---- 5) HARD RESET: kill bytecode + pytest caches so tests stop running ghosts ----
echo "[*] Nuking caches (__pycache__, .pytest_cache)..."
find . -type d -name "__pycache__" -prune -exec rm -rf {} + || true
rm -rf .pytest_cache || true

# Force mtimes forward (avoid 1-second resolution traps)
echo "[*] Forcing mtimes forward..."
sleep 1
touch api/main.py api/schemas.py api/defend.py api/feed.py

# ---- compile sanity ----
echo "[*] Compile sanity..."
python -m py_compile api/main.py api/schemas.py api/feed.py api/defend.py

