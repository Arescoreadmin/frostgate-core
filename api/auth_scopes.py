from __future__ import annotations

import os
from typing import Iterable, Set, Tuple

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.exc import OperationalError

from api.db import get_db

ERR_INVALID = "Invalid or missing API key"


def _truthy(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_scopes(scopes_csv: str | None) -> Set[str]:
    if not scopes_csv:
        return set()
    return {s.strip() for s in scopes_csv.split(",") if s.strip()}


def _has_scopes(granted: Set[str], required: Iterable[str]) -> bool:
    if "*" in granted:
        return True
    req = set(required)
    return req.issubset(granted)


def _auth_enabled_for_request(request: Request) -> bool:
    """
    Priority:
      1) request.app.state.auth_enabled (if set)
      2) FG_AUTH_ENABLED env override (only if explicitly set)
      3) fallback: enabled iff FG_API_KEY exists
    """
    st = getattr(request.app, "state", None)
    state_val = getattr(st, "auth_enabled", None) if st is not None else None

    base = bool(state_val) if state_val is not None else bool(os.getenv("FG_API_KEY"))

    if "FG_AUTH_ENABLED" in os.environ:
        return _truthy(os.getenv("FG_AUTH_ENABLED"), default=base)
    return base


def verify_api_key_raw(api_key: str) -> Tuple[bool, Set[str]]:
    """
    Returns (ok, scopes).
    - accepts "supersecret" (dev/test)
    - accepts FG_API_KEY (global key)
    - otherwise DB-backed keys if available
    """
    if not api_key:
        return False, set()

    if api_key == "supersecret":
        return True, {"*"}  # dev/test wildcard

    expected = os.getenv("FG_API_KEY")
    if expected and api_key == expected:
        return True, {"*"}

    # Try DB-backed keys
    try:
        from api.db_models import ApiKey, hash_api_key
    except Exception:
        return False, set()

    key_hash = hash_api_key(api_key)

    try:
        db = next(get_db())
        row = (
            db.query(ApiKey)
            .filter(ApiKey.key_hash == key_hash)
            .filter(ApiKey.enabled.is_(True))
            .first()
        )
        if not row:
            return False, set()
        return True, _split_scopes(row.scopes_csv)
    except (OperationalError, Exception):
        return False, set()


def _get_candidate_key(
    x_api_key: str | None,
    cookie_key: str | None,
) -> str | None:
    # Header wins, cookie fallback
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if cookie_key and cookie_key.strip():
        return cookie_key.strip()
    return None


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    fg_api_key: str | None = Cookie(default=None, alias=None),
) -> Set[str]:
    """
    Dependency: returns granted scopes or raises 401.
    Enforces only when auth is enabled.
    """
    enabled = _auth_enabled_for_request(request)
    if not enabled:
        return {"*"}

    # Cookie name is configurable; default "fg_api_key"
    cookie_name = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")
    cookie_val = request.cookies.get(cookie_name) or fg_api_key

    key = _get_candidate_key(x_api_key, cookie_val)
    if not key:
        raise HTTPException(status_code=401, detail=ERR_INVALID)

    ok, scopes = verify_api_key_raw(key)
    if not ok:
        raise HTTPException(status_code=401, detail=ERR_INVALID)

    return scopes


def verify_api_key_always(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    fg_api_key: str | None = Cookie(default=None, alias=None),
) -> Set[str]:
    """
    ALWAYS enforce a valid key. Header or cookie accepted.
    """
    cookie_name = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")
    cookie_val = request.cookies.get(cookie_name) or fg_api_key

    key = _get_candidate_key(x_api_key, cookie_val)
    if not key:
        raise HTTPException(status_code=401, detail=ERR_INVALID)

    ok, scopes = verify_api_key_raw(key)
    if not ok:
        raise HTTPException(status_code=401, detail=ERR_INVALID)
    return scopes


def require_api_key_always(scopes: Set[str] = Depends(verify_api_key_always)) -> None:
    return None


def require_api_key(scopes: Set[str] = Depends(verify_api_key)) -> None:
    return None


def require_scopes(*required_scopes: str):
    def _dep(scopes: Set[str] = Depends(verify_api_key)) -> None:
        if not _has_scopes(scopes, required_scopes):
            raise HTTPException(status_code=403, detail="forbidden")
        return None
    return _dep
