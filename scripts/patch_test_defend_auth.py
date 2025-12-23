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
    for attr in ("key_hash", "api_key_hash", "hashed_key", "key_digest"):
        if hasattr(obj, attr):
            setattr(obj, attr, hashed)
            break
    else:
        raise RuntimeError("ApiKey model has no recognized hash field")

    if hasattr(obj, "scopes"):
        obj.scopes = scopes
    if hasattr(obj, "is_active"):
        obj.is_active = True

    db.add(obj)
    db.commit()
    return raw
'''

# Inject helper near the top (after imports)
if "_mk_key(" not in s:
    s = re.sub(r"(?m)^(import .*|from .*import .*)\n(?!import|from)",
               lambda m: m.group(0) + "\n" + helper.strip("\n") + "\n\n",
               s, count=1)

# Ensure the failing request includes X-API-Key
# We’ll replace any client.post("/defend"... ) call that lacks headers with one that includes headers.
# This is conservative: only touches the first occurrence.
pat_call = re.compile(r'client\.post\(\s*"/defend"([^)]*)\)')
m = pat_call.search(s)
if not m:
    raise SystemExit("[FAIL] Could not find client.post(\"/defend\"...) call to patch")

call_args = m.group(1)
if "headers=" in call_args:
    print("[SKIP] /defend call already has headers=")
else:
    # We need db access: test suite usually has get_db available
    # We'll assume the test has access to get_db() similar to ingest test.
    inject_db = r'''
    db_gen = get_db()
    db = next(db_gen)
    api_key = _mk_key(db, scopes="defend:write")
'''
    if "get_db" not in s:
        # Add imports if missing
        if "from api.db import get_db" not in s:
            s = "from api.db import get_db\n" + s

    # Insert the DB key creation right before the first /defend request in the file
    idx = m.start()
    s = s[:idx] + inject_db + "\n" + s[idx:]

    # Patch the call
    new_call = f'client.post("/defend"{call_args}, headers={{"X-API-Key": api_key}})'
    s = s[:m.start()] + new_call + s[m.end():]

p.write_text(s, encoding="utf-8")
print("[OK] Patched tests/test_defend_endpoint.py to create scoped API key + send header")
