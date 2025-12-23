#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re

p = Path("tests/test_defend_endpoint.py")
s = p.read_text(encoding="utf-8")

helper = r'''
def _mk_key(db, scopes="defend:write"):
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

# Replace any existing _mk_key block, otherwise insert after imports.
pat = re.compile(r"(?ms)^\s*def\s+_mk_key\s*\(.*?\)\s*:\s*.*?\n(?=^\s*def\s|\Z)")
if pat.search(s):
    s = pat.sub(helper.strip("\n") + "\n\n", s, count=1)
else:
    s = re.sub(r"(?m)^(import .*|from .*import .*)\n(?!import|from)",
               lambda m: m.group(0) + "\n" + helper.strip("\n") + "\n\n",
               s, count=1)

p.write_text(s, encoding="utf-8")
print("[OK] Patched tests/test_defend_endpoint.py _mk_key() for prefix/key_hash/scopes_csv/enabled")
