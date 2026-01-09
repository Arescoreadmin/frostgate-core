from __future__ import annotations

import hashlib
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def insert_api_key(
    engine: Engine,
    *,
    name: str | None,
    raw_key: str,
    scopes: Iterable[str] | str,
    enabled: bool = True,
) -> dict:
    """
    Insert an API key row into api_keys using current schema:
      prefix (NOT NULL), key_hash (NOT NULL), scopes_csv (NOT NULL), enabled (NOT NULL)

    Returns: dict of inserted row (id/prefix/key_hash/scopes_csv/enabled), best effort.
    """
    raw_key = str(raw_key).strip()
    if not raw_key:
        raise ValueError("raw_key cannot be empty")

    # prefix: everything before first '_' + '_' fallback first 8 chars + '_'
    if "_" in raw_key:
        prefix = raw_key.split("_", 1)[0] + "_"
    else:
        prefix = raw_key[:8] + "_"

    key_hash = _sha256_hex(raw_key)

    if isinstance(scopes, str):
        scopes_csv = scopes.strip()
    else:
        scopes_csv = ",".join(
            sorted({s.strip() for s in scopes if s and str(s).strip()})
        )

    sql = text(
        """
        INSERT INTO api_keys (name, prefix, key_hash, scopes_csv, enabled)
        VALUES (:name, :prefix, :key_hash, :scopes_csv, :enabled)
        RETURNING id, name, prefix, key_hash, scopes_csv, enabled
        """
    )

    with engine.begin() as conn:
        row = (
            conn.execute(
                sql,
                dict(
                    name=name,
                    prefix=prefix,
                    key_hash=key_hash,
                    scopes_csv=scopes_csv,
                    enabled=enabled,
                ),
            )
            .mappings()
            .first()
        )

    return (
        dict(row)
        if row
        else {
            "prefix": prefix,
            "key_hash": key_hash,
            "scopes_csv": scopes_csv,
            "enabled": enabled,
        }
    )
