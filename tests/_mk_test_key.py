from __future__ import annotations

import uuid

from api.db import get_db
from api.db_models import ApiKey, hash_api_key


def mint_key(scopes_csv: str, *, enabled: bool = True, name: str = "pytest") -> str:
    """
    Create a DB-backed API key matching current schema:
      prefix, key_hash, scopes_csv, enabled.
    Returns the RAW key to use as X-API-Key / x-api-key header.
    """
    raw = "TEST_" + uuid.uuid4().hex
    kh = hash_api_key(raw)

    db = next(get_db())
    obj = ApiKey(
        name=name,
        prefix=raw[:16],
        key_hash=kh,
        scopes_csv=scopes_csv,
        enabled=enabled,
    )
    db.add(obj)
    db.commit()
    return raw
