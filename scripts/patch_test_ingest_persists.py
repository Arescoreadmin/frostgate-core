#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

p = Path("scripts/test_ingest_persists.py")
s = p.read_text(encoding="utf-8")

# Replace ONLY the _create_api_key function with a safe ORM-based implementation.
new_fn = r'''
def _create_api_key(db):
    """
    Create an API key row compatible with current ApiKey ORM/table schema.
    Avoids hardcoding columns like (key, scopes, is_active) which may not exist.
    """
    import uuid
    from api.db_models import ApiKey, hash_api_key

    raw = f"TEST_{uuid.uuid4().hex}"
    cols = set(ApiKey.__table__.columns.keys())

    kwargs = {}
    if "key_hash" in cols:
        kwargs["key_hash"] = hash_api_key(raw)
    if "prefix" in cols:
        kwargs["prefix"] = raw.split("_", 1)[0] + "_" if "_" in raw else raw[:8]
    if "scopes_csv" in cols:
        kwargs["scopes_csv"] = "ingest:write"
    if "enabled" in cols:
        kwargs["enabled"] = True
    if "is_active" in cols:
        kwargs["is_active"] = True

    row = ApiKey(**kwargs)
    db.add(row)
    db.commit()
    return raw
'''.strip() + "\n\n"

s2, n = re.subn(
    r"def\s+_create_api_key\s*\(.*?\)\s*:\n(?:^[ \t].*\n)+?(?=^\S|\Z)",
    new_fn,
    s,
    flags=re.MULTILINE,
)

if n == 0:
    raise SystemExit("[FAIL] Could not find/replace _create_api_key in scripts/test_ingest_persists.py")

p.write_text(s2, encoding="utf-8")
print("[OK] Patched scripts/test_ingest_persists.py _create_api_key() to ORM-based insert")
