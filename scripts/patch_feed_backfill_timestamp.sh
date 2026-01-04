#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-api/feed.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re
import py_compile

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Ensure we have datetime import (used for fallback timestamp)
if "from datetime import datetime, timezone" not in s:
    # insert near top after existing imports
    s = re.sub(r'(?m)^(import\s+re\s*)$', r'\1\nfrom datetime import datetime, timezone', s, count=1)

# Patch inside _backfill_feed_item: right before "return i"
pat = re.compile(r'(?ms)^def _backfill_feed_item\(i: dict\) -> dict:\n(.*?)(^\s*return i\s*$)', re.M)
m = pat.search(s)
if not m:
    raise SystemExit("PATCH FAILED: couldn't find _backfill_feed_item")

body = m.group(1)
ret  = m.group(2)

# If we already added a timestamp fallback previously, don't duplicate
if "timestamp fallback" not in body:
    inject = r'''
    # timestamp fallback (never ship nulls to UI)
    if not i.get("timestamp"):
        # try common keys in metadata
        meta = i.get("metadata") or {}
        for k in ("timestamp", "ts", "time", "created_at", "created_at_utc", "event_time"):
            v = meta.get(k) if isinstance(meta, dict) else None
            if v:
                i["timestamp"] = v
                break
        # try diff structure if present
        if not i.get("timestamp"):
            diff = i.get("decision_diff") or {}
            if isinstance(diff, dict):
                v = diff.get("timestamp") or diff.get("ts")
                if v:
                    i["timestamp"] = v
        # last resort: now (UTC)
        if not i.get("timestamp"):
            i["timestamp"] = datetime.now(timezone.utc).isoformat()
'''
    body = body + inject

s2 = s[:m.start()] + "def _backfill_feed_item(i: dict) -> dict:\n" + body + ret + s[m.end():]
p.write_text(s2, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("✅ Patched _backfill_feed_item: timestamp fallback added")
PY
