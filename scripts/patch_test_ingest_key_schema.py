#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re

p = Path("scripts/test_ingest_persists.py")
s = p.read_text(encoding="utf-8")

new_fn = r'''
def _create_api_key(db, scopes="ingest:write"):
    """
    Create an API key row compatible with current api_keys schema:
    prefix, key_hash, scopes_csv, enabled.
    Returns RAW key to use as X-API-Key.
    """
    import uuid
    from api.db_models import ApiKey, hash_api_key

    raw = "TEST_" + uuid.uuid4().hex
    hashed = hash_api_key(raw)

    obj = ApiKey()
    obj.prefix = raw[:16]
    obj.key_hash = hashed
    obj.scopes_csv = scopes
    obj.enabled = True

    db.add(obj)
    db.commit()
    return raw
'''

pat = re.compile(r"(?ms)^\s*def\s+_create_api_key\s*\(.*?\)\s*:\s*.*?\n(?=^\s*def\s|\Z)")
m = pat.search(s)
if not m:
    raise SystemExit("[FAIL] Could not find _create_api_key() to replace")

s = s[:m.start()] + new_fn.strip("\n") + "\n\n" + s[m.end():]
p.write_text(s, encoding="utf-8")
print("[OK] Patched scripts/test_ingest_persists.py _create_api_key() for prefix/key_hash/scopes_csv/enabled")
