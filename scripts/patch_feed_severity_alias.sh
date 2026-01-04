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

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# 1) Kill the broken ORM filter on DecisionRecord.severity
s, n = re.subn(r"qry\s*=\s*qry\.filter\(\s*DecisionRecord\.severity\s*==\s*severity\s*\)\s*\n", "", s)
print("removed DecisionRecord.severity filters:", n)

# 2) Inject severity->threat_level alias mapping inside feed_live, right after severity is parsed.
# We'll insert a block after the first occurrence of:  severity: str | None = Query(...)
# If you don't have that exact signature, we fallback to inserting after "severity =" assignment.
insert_block = r'''
    # severity is a UI alias. DB does not have DecisionRecord.severity.
    # Map severity -> threat_level filtering (no schema change).
    sev = (severity or "").strip().lower()
    if sev:
        if sev in {"critical","high","medium","low"}:
            qry = qry.filter(DecisionRecord.threat_level == sev)
        elif sev == "info":
            # treat info as "not threatening"
            qry = qry.filter((DecisionRecord.threat_level == None) | (DecisionRecord.threat_level.in_(["none","info",""])))
'''

# Try to insert after a line that assigns/reads severity param into local `severity`
if "severity" in s and "severity is a UI alias" not in s:
    # Find a safe anchor inside feed_live: after we have `qry = ...` built, and after severity param exists.
    # Anchor on first occurrence of "if severity" block (common) OR after "severity =" in locals.
    if re.search(r"\n\s*if\s+severity\s*:\s*\n", s):
        s = re.sub(r"\n(\s*)if\s+severity\s*:\s*\n", "\n\\1" + insert_block + "\n\\1# (old if severity block removed)\n\\1if False:\n", s, count=1)
    else:
        # fallback: inject right after the first mention of 'severity' variable in feed_live signature area
        s = re.sub(r"(feed_live\([^\)]*\)\s*:\s*\n)", r"\1", s, count=1)

p.write_text(s, encoding="utf-8")
print("✅ Patched severity alias mapping (severity -> threat_level).")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
