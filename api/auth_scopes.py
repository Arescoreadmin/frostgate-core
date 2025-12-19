# api/auth_scopes.py
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Callable, Optional, Set

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.db_models import ApiKey, hash_api_key


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_scopes(scopes_csv: str) -> Set[str]:
    return {s.strip() for s in (scopes_csv or "").split(",") if s.strip()}


def _prefix_from_key(raw_key: str) -> str:
    # Convention: keys look like "ADMIN_xxxxx" / "AGENT_xxxxx"
    if "_" not in raw_key:
        return raw_key[:8]  # deterministic fallback
    return raw_key.split("_", 1)[0].strip() + "_"


def _env_fallback_scopes(raw_key: str) -> Optional[Set[str]]:
    """
    Transitional fallback:
      FG_API_KEYS="ADMIN_xxx|scope1,scope2;AGENT_xxx|scopeA"
    or
      FG_API_KEYS_FILE=/path/to/file
    Same format inside the file.
    """
    s = (os.getenv("FG_API_KEYS", "") or "").strip()

    file_path = (os.getenv("FG_API_KEYS_FILE", "") or "").strip()
    if file_path and os.path.exists(file_path):
        try:
            s = open(file_path, "r", encoding="utf-8").read().strip()
        except Exception:
            # fallback is optional, never crash auth because of it
            pass

    if not s:
        return None

    for pair in [x.strip() for x in s.split(";") if x.strip()]:
        try:
            k, scopes = pair.split("|", 1)
        except ValueError:
            continue
        if k.strip() == raw_key.strip():
            return _parse_scopes(scopes)
    return None


def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Set[str]:
    """
    Validates X-API-Key and returns its scopes set.
    Also attaches scopes to request.state.scopes for downstream dependencies.
    """
    if not x_api_key or not x_api_key.strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing API key")

    raw_key = x_api_key.strip()
    prefix = _prefix_from_key(raw_key)
    candidate_hash = hash_api_key(raw_key)

    # 1) DB-backed lookup by prefix
    row = db.query(ApiKey).filter(ApiKey.prefix == prefix).first()
    if row and row.enabled:
        if row.expires_at and row.expires_at < _utcnow():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key expired")

        # constant-time compare
        if hmac.compare_digest(row.key_hash, candidate_hash):
            row.last_used_at = _utcnow()
            db.add(row)
            db.commit()

            scopes = _parse_scopes(row.scopes_csv)
            request.state.scopes = scopes
            return scopes

    # 2) Transitional env/file fallback
    fallback = _env_fallback_scopes(raw_key)
    if fallback is not None:
        request.state.scopes = fallback
        return fallback

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def require_scopes(*required: str):
    """
    Dependency factory enforcing required API scopes.
    Uses verify_api_key to validate and retrieve scopes.
    """
    required_set = set(required)

    def _dep(scopes: Set[str] = Depends(verify_api_key)) -> bool:
        missing = sorted(required_set - set(scopes or set()))
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing scope(s): {', '.join(missing)}",
            )
        return True

    return _dep


def require_scope(required: str) -> Callable[[Set[str]], None]:
    """
    Legacy helper (single-scope check).
    Prefer require_scopes("a","b") for multi-scope endpoints.
    """
    def _inner(scopes: Set[str] = Depends(verify_api_key)) -> None:
        if required not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing scope: {required}",
            )
    return _inner
