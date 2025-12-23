#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re

p = Path("scripts/test_ingest_persists.py")
s = p.read_text(encoding="utf-8")

# Replace the whole _create_api_key function with a schema-safe ORM version.
new_fn = r'''
def _create_api_key(db, scopes="ingest:write"):
    """
    Create an API key row compatible with current ApiKey ORM/table schema.
    Avoids hardcoding columns like (key, scopes, is_active).
    Returns the RAW key to use in X-API-Key header.
    """
    import uuid
    from api.db_models import ApiKey, hash_api_key

    raw = "TEST_" + uuid.uuid4().hex
    hashed = hash_api_key(raw)

    obj = ApiKey()

    # set hash field (schema varies)
    for attr in ("key_hash", "api_key_hash", "hashed_key", "key_digest"):
        if hasattr(obj, attr):
            setattr(obj, attr, hashed)
            break
    else:
        raise RuntimeError("ApiKey model has no recognized hash field (key_hash/api_key_hash/...)")

    # set scopes (schema varies)
    if hasattr(obj, "scopes"):
        setattr(obj, "scopes", scopes)
    elif hasattr(obj, "scopes_json"):
        setattr(obj, "scopes_json", [s.strip() for s in scopes.split(",") if s.strip()])
    # else: some schemas derive scopes elsewhere, we leave it.

    # set is_active if present
    if hasattr(obj, "is_active"):
        setattr(obj, "is_active", True)

    db.add(obj)
    db.commit()
    return raw
'''

pat = re.compile(r"(?ms)^\s*def\s+_create_api_key\s*\(.*?\)\s*:\s*.*?\n(?=^\s*def\s|\Z)")
m = pat.search(s)
if not m:
    raise SystemExit("[FAIL] Could not find _create_api_key() in scripts/test_ingest_persists.py")

s2 = s[:m.start()] + new_fn.strip("\n") + "\n\n" + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("[OK] Patched scripts/test_ingest_persists.py _create_api_key() to ORM-based insert")
