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

# --- 1) Remove any broken DB filter referencing DecisionRecord.severity ---
s, n1 = re.subn(r"^\s*qry\s*=\s*qry\.filter\(\s*DecisionRecord\.severity\s*==\s*severity\s*\)\s*\n", "", s, flags=re.M)

# --- 2) Ensure we have a safe, computed "effective severity" filter ---
# We will:
# - keep 'severity' param for UI
# - but filter based on computed severity from threat_level (or other fields if present)
#
# Insert a block right after the query is constructed (after the first 'qry =' in feed_live).
m = re.search(r"(?m)^(?P<indent>\s*)def\s+feed_live\s*\(.*?\)\s*:\s*$", s)
if not m:
    raise SystemExit("PATCH FAILED: couldn't find def feed_live(...) in api/feed.py")

# Find first "qry =" after feed_live definition
feed_start = m.end()
m_qry = re.search(r"(?m)^(?P<qindent>\s*)qry\s*=\s*.*$", s[feed_start:])
if not m_qry:
    raise SystemExit("PATCH FAILED: couldn't find 'qry =' inside feed_live")

q_abs = feed_start + m_qry.end()
indent = m_qry.group("qindent")  # indentation inside function

block = f"""
{indent}# --- UI severity is an alias (DB has no DecisionRecord.severity). ---
{indent}# We interpret severity as computed from threat_level when filtering.
{indent}sev = (severity or "").strip().lower()
{indent}if sev:
{indent}    if sev in {{"critical","high","medium","low"}}:
{indent}        # Map directly to threat_level
{indent}        qry = qry.filter(DecisionRecord.threat_level == sev)
{indent}    elif sev == "info":
{indent}        # Treat info as non-threatening / informational
{indent}        qry = qry.filter((DecisionRecord.threat_level == None) | (DecisionRecord.threat_level.in_(["none","info",""])))
"""

# Only insert if we haven't already added it
if "UI severity is an alias" not in s:
    s = s[:q_abs] + block + s[q_abs:]

# --- 3) Apply backfill right before returning JSON ---
# We want: items = [_backfill_feed_item(i) for i in items] immediately before return {"items": items, ...}
if "_backfill_feed_item" not in s:
    raise SystemExit("PATCH FAILED: _backfill_feed_item not found in api/feed.py (your earlier helper injection didn't stick).")

# Insert backfill line before the first return containing '"items": items'
pat_return = r"(?m)^(?P<rindent>\s*)return\s+\{\s*['\"]items['\"]\s*:\s*items\s*,"
m_ret = re.search(pat_return, s)
if not m_ret:
    raise SystemExit("PATCH FAILED: couldn't find return {'items': items, ...} in api/feed.py")

rindent = m_ret.group("rindent")
backfill_line = f"{rindent}items = [_backfill_feed_item(i) for i in items]\n"

# Avoid double insertion
pre = s[:m_ret.start()]
if "items = [_backfill_feed_item(i) for i in items]" not in pre[-400:]:
    s = s[:m_ret.start()] + backfill_line + s[m_ret.start():]

p.write_text(s, encoding="utf-8")
print(f"✅ Patched api/feed.py (removed bad severity DB filter: {n1}, added severity alias + backfill-before-return)")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
