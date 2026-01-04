#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/feed.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from __future__ import annotations
from pathlib import Path
import re, py_compile

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# 1) Ensure datetime import exists (for isoformat safety, not strictly needed)
if "from datetime import datetime, timezone" not in s:
    # insert after first block of imports (best-effort)
    s = re.sub(r'(?m)^(from __future__ import annotations\s*\n)', r'\1from datetime import datetime, timezone\n', s, count=1)

# 2) Find feed_live block
m = re.search(r'(?ms)^def feed_live\([^)]*\):\n(.*)$', s)
if not m:
    raise SystemExit("PATCH FAILED: couldn't find def feed_live(...)")

# 3) Add per-row computed timestamp right after "for r in rows:"
needle = r"\n\s*for r in rows:\n"
if re.search(needle, s) and "created_at" in s:
    s = re.sub(
        needle,
        lambda mm: mm.group(0) + "          # DB uses created_at; API exposes timestamp\n"
                              "          ts = getattr(r, \"created_at\", None)\n"
                              "          ts_iso = ts.isoformat() if ts else None\n",
        s,
        count=1
    )
else:
    raise SystemExit("PATCH FAILED: couldn't find loop 'for r in rows:'")

# 4) Replace any explicit timestamp None assignment in the item construction
#    (handles both dict literal and pydantic model init patterns)
s, n1 = re.subn(r'(?m)^\s*timestamp\s*=\s*None\s*$', r'    timestamp = ts_iso', s)
s, n2 = re.subn(r'(?m)(^\s*"timestamp"\s*:\s*)None(\s*,\s*$)', r'\1ts_iso\2', s)

# 5) If there is no timestamp field at all in item building, inject it near threat_level line
if n1 == 0 and n2 == 0:
    # try inject into dict literal: after '"threat_level": ...'
    s2, n3 = re.subn(
        r'(?m)^(\s*"threat_level"\s*:\s*[^,]+,\s*)$',
        r'\1\n        "timestamp": ts_iso,\n',
        s,
        count=1
    )
    if n3 == 0:
        # try inject into FeedItem(...) init: after 'threat_level='
        s2, n4 = re.subn(
            r'(?m)^(\s*threat_level\s*=\s*[^,]+,\s*)$',
            r'\1\n        timestamp=ts_iso,\n',
            s,
            count=1
        )
        if n4 == 0:
            raise SystemExit("PATCH FAILED: couldn't inject timestamp into item builder")
        s = s2
    else:
        s = s2

p.write_text(s, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("✅ Patched feed_live: timestamp now derived from DecisionRecord.created_at")
PY
