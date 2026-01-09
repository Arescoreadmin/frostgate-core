from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from typing import Callable, Optional, Set
from api.db import _resolve_sqlite_path

from fastapi import Depends, Header, HTTPException, Request
from api.db import init_db

import logging

log = logging.getLogger("frostgate")


def _b64url(b: bytes) -> str:
    """Base64url encode bytes, no padding."""
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


ERR_INVALID = "Invalid or missing API key"
DEFAULT_TTL_SECONDS = 24 * 3600


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _b64url_json(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _parse_scopes_csv(val) -> Set[str]:
    if not val:
        return set()
    if isinstance(val, (list, tuple, set)):
        return {str(x).strip() for x in val if str(x).strip()}
    s = str(val).strip()
    if not s:
        return set()
    return {x.strip() for x in s.split(",") if x.strip()}


def _extract_key(request: Request, x_api_key: Optional[str]) -> Optional[str]:
    # Header first
    if x_api_key and str(x_api_key).strip():
        return str(x_api_key).strip()

    # Cookie (UI)
    cookie_name = (
        os.getenv("FG_UI_COOKIE_NAME") or "fg_api_key"
    ).strip() or "fg_api_key"
    ck = (request.cookies.get(cookie_name) or "").strip()
    if ck:
        return ck

    # Query (dev convenience)
    qp = request.query_params
    qk = (qp.get("api_key") or qp.get("key") or "").strip()
    if qk:
        return qk

    return None


def mint_key(
    *scopes: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    tenant_id: Optional[str] = None,
    now: Optional[int] = None,
    secret: Optional[str] = None,
) -> str:
    """
    Mint a key and persist it into sqlite table `api_keys`:
      api_keys(prefix, key_hash, scopes_csv, enabled)

    Returned key format (NEW):
      <prefix>.<token>.<secret>

    Where:
      key_hash stored = sha256(secret)
      token is base64url(json payload)
    """
    sqlite_path = (os.getenv("FG_SQLITE_PATH") or "").strip()
    if not sqlite_path:
        sqlite_path = str(_resolve_sqlite_path())

    # Ensure schema exists in the exact sqlite file (safe/idempotent).
    # This prevents "no such table: api_keys/decisions" when tests call mint_key early.
    try:
        init_db(sqlite_path=sqlite_path)
    except Exception:
        # Best effort: mint_key should still fail later if DB truly unusable,
        # but schema init errors shouldn't crash import-time.
        log.exception("init_db failed in mint_key (best effort)")

    now_i = int(now) if now is not None else int(time.time())
    exp_i = now_i + int(ttl_seconds)

    if secret is None:
        secret = secrets.token_urlsafe(32)

    prefix = "fgk"
    payload = {
        "scopes": list(scopes),
        "tenant_id": tenant_id,
        "iat": now_i,
        "exp": exp_i,
    }

    token = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    key_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    scopes_csv = ",".join(scopes)

    # Persist key into sqlite (schema-aware)
    con = sqlite3.connect(sqlite_path)
    try:
        cols = con.execute("PRAGMA table_info(api_keys)").fetchall()
        # (cid, name, type, notnull, dflt_value, pk)
        names = [r[1] for r in cols]
        notnull = {r[1] for r in cols if int(r[3] or 0) == 1 and r[4] is None}

        values = {
            "prefix": prefix,
            "key_hash": key_hash,
            "scopes_csv": scopes_csv,
            "enabled": 1,
        }

        # Newer schema requires name (NOT NULL)
        if "name" in names:
            values["name"] = "minted:" + (scopes_csv or "none")

        # Optional schema evolution support
        if "tenant_id" in names:
            values["tenant_id"] = tenant_id
        if "created_at" in names and "created_at" in notnull:
            values["created_at"] = now_i

        ordered = [
            c
            for c in (
                "name",
                "prefix",
                "key_hash",
                "scopes_csv",
                "tenant_id",
                "created_at",
                "enabled",
            )
            if c in names and c in values
        ]
        if not ordered:
            raise RuntimeError("api_keys table has no usable columns for insert")

        qcols = ",".join(ordered)
        qmarks = ",".join(["?"] * len(ordered))
        params = tuple(values[c] for c in ordered)
        con.execute(f"INSERT INTO api_keys({qcols}) VALUES({qmarks})", params)
        con.commit()
    finally:
        con.close()

    return f"{prefix}.{token}.{secret}"


def verify_api_key_raw(
    raw: Optional[str] = None,
    required_scopes=None,
    raw_key: Optional[str] = None,
    db=None,
    **_ignored,
) -> bool:
    """
    Verifies:
      1) Global FG_API_KEY matches exactly
      2) DB-backed keys in sqlite `api_keys` table

    Supports TWO DB key formats:
      A) NEW: <prefix>.<token>.<secret>
         - prefix stored as `prefix`
         - key_hash stored = sha256(secret)
      B) LEGACY (tests): raw="TEST_<uuidhex>" (no dots)
         - prefix stored = raw[:16]
         - key_hash stored = api.db_models.hash_api_key(raw)
    """
    raw = (raw or raw_key or "").strip()

    # 1) global key bypass
    global_key = (os.getenv("FG_API_KEY") or "").strip()
    if raw and global_key and raw == global_key:
        return True
    if not raw:
        return False

    sqlite_path = (os.getenv("FG_SQLITE_PATH") or "").strip()
    if not sqlite_path:
        return False

    def _row_for(prefix: str, key_hash: str):
        con = sqlite3.connect(sqlite_path)
        try:
            try:
                return con.execute(
                    "select scopes_csv, enabled from api_keys where prefix=? and key_hash=? limit 1",
                    (prefix, key_hash),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        finally:
            con.close()

    scopes_csv = None
    enabled = None

    parts = raw.split(".")
    if len(parts) >= 3:
        # NEW: prefix.token.secret
        prefix = parts[0]
        secret_val = parts[-1]
        row = _row_for(prefix, _sha256_hex(secret_val))
        if row:
            scopes_csv, enabled = row
    else:
        # LEGACY: raw key stored hashed by api.db_models.hash_api_key(raw), prefix=raw[:16]
        prefix = raw[:16]
        try:
            from api.db_models import (
                hash_api_key as _hash_api_key,
            )  # matches tests/_mk_test_key.py

            legacy_hash = _hash_api_key(raw)
        except Exception:
            # fallback to something deterministic; shouldn't be needed if api.db_models exists
            legacy_hash = _sha256_hex(raw)

        row = _row_for(prefix, legacy_hash)
        if row:
            scopes_csv, enabled = row

    if scopes_csv is None or enabled is None:
        return False
    if not int(enabled):
        return False

    # Scope enforcement (if requested)
    if required_scopes is None:
        return True

    needed = (
        set(required_scopes)
        if isinstance(required_scopes, (set, list, tuple))
        else {str(required_scopes)}
    )
    needed = {s.strip() for s in needed if str(s).strip()}
    if not needed:
        return True

    have = _parse_scopes_csv(scopes_csv)
    if "*" in have:
        return True

    return needed.issubset(have)


def require_api_key_always(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    required_scopes: Set[str] | None = None,
) -> str:
    got = _extract_key(request, x_api_key)
    if not got:
        raise HTTPException(status_code=401, detail=ERR_INVALID)

    if not verify_api_key_raw(got, required_scopes=required_scopes):
        raise HTTPException(status_code=401, detail=ERR_INVALID)

    return got


def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    # compatibility dep expected by modules
    return require_api_key_always(request, x_api_key, required_scopes=None)


def require_scopes(*scopes: str) -> Callable[..., None]:
    """
    Returns a dependency that enforces the provided scopes.

    IMPORTANT: No untyped lambda params.
    If you use `lambda request, ...` without type hints, FastAPI may treat `request`
    as a query param and you'll see: {"loc":["query","request"],"msg":"Field required"}.
    """
    needed: Set[str] = {str(s).strip() for s in scopes if str(s).strip()}

    if not needed:

        def _noop() -> None:
            return None

        return _noop

    def _scoped_key_dep(
        request: Request,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> str:
        return require_api_key_always(request, x_api_key, required_scopes=needed)

    def _dep(_: str = Depends(_scoped_key_dep)) -> None:
        return None

    return _dep
