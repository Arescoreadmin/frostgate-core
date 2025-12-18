from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

log = logging.getLogger("frostgate")

API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)

# Env formats supported:
# 1) FG_API_KEYS="KEY1|scopeA,scopeB;KEY2|scopeC"
# 2) Legacy: FG_API_KEY="KEY" (treated as admin-ish)
# 3) Optional: FG_ADMIN_KEY / FG_AGENT_KEY (we'll also fold these in if present)


@dataclass(frozen=True)
class Principal:
    raw_key: str
    key_id: str
    scopes: FrozenSet[str]


def _key_id(k: str) -> str:
    return (k or "")[:12]


def _parse_scoped_keys_env() -> Dict[str, Principal]:
    principals: Dict[str, Principal] = {}

    # Primary scoped keys
    raw = (os.getenv("FG_API_KEYS") or "").strip()
    if raw:
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            if "|" not in entry:
                # If someone fat-fingers the format, ignore the entry (and log it)
                log.warning("FG_API_KEYS entry missing '|': %r", entry)
                continue
            key, scope_str = entry.split("|", 1)
            key = key.strip()
            if not key:
                continue
            scopes = frozenset(s.strip() for s in scope_str.split(",") if s.strip())
            principals[key] = Principal(raw_key=key, key_id=_key_id(key), scopes=scopes)

    # Legacy single key fallback
    legacy = (os.getenv("FG_API_KEY") or "").strip()
    if legacy and legacy not in principals:
        # Give it broad scopes so old setups keep working
        principals[legacy] = Principal(
            raw_key=legacy,
            key_id=_key_id(legacy),
            scopes=frozenset({"decisions:read", "defend:write", "ingest:write"}),
        )

    # Optional explicit keys (helpful for dev)
    for env_name, default_scopes in [
        ("FG_ADMIN_KEY", frozenset({"decisions:read", "defend:write", "ingest:write"})),
        ("FG_AGENT_KEY", frozenset({"decisions:read", "ingest:write"})),
    ]:
        k = (os.getenv(env_name) or "").strip()
        if k and k not in principals:
            principals[k] = Principal(raw_key=k, key_id=_key_id(k), scopes=default_scopes)

    # Log safely (no raw keys)
    for p in principals.values():
        log.info("auth principal=%s scopes=%s", p.key_id, sorted(p.scopes))

    return principals


_PRINCIPALS: Dict[str, Principal] = _parse_scoped_keys_env()


async def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> Principal:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    p = _PRINCIPALS.get(api_key)
    if not p:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return p


def require_scope(scope: str) -> Callable:
    async def _dep(p: Principal = Depends(verify_api_key)) -> Principal:
        if scope not in p.scopes:
            raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")
        return p

    return _dep
