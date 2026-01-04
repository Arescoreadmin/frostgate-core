#!/usr/bin/env bash
set -euo pipefail

FILE="api/feed.py"

echo "Searching for latest compilable backup..."
best=""
for f in $(ls -1t api/feed.py.bak.* 2>/dev/null || true); do
  cp -a "$f" "$FILE"
  if python -m py_compile "$FILE" >/dev/null 2>&1; then
    best="$f"
    break
  fi
done

if [[ -z "${best}" ]]; then
  echo "ERROR: No compilable api/feed.py.bak.* found." >&2
  exit 1
fi

echo "✅ Restored from: $best"

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Find the @router.get("/live"... ) decorator block
m = re.search(r'(?m)^\s*@router\.get\(\s*[\'"]\/live[\'"].*\)\s*$', s)
if not m:
    raise SystemExit("PATCH FAILED: could not find @router.get('/live', ...) in api/feed.py")

# Find the function def that follows it
mdef = re.search(r'(?m)^\s*(async\s+def|def)\s+feed_live\s*\(', s[m.end():])
if not mdef:
    raise SystemExit("PATCH FAILED: could not find def feed_live(...) after /live decorator")

def_start = m.end() + mdef.start()

# Find end of function: next top-level decorator or def at same/less indentation
tail = s[def_start:]
lines = tail.splitlines(True)

# Determine indentation of def line
def_line = lines[0]
def_indent = re.match(r'^(\s*)', def_line).group(1)

end_off = len(tail)
cur = len(lines[0])
for ln in lines[1:]:
    # next decorator or next def at same/less indentation ends function
    if re.match(r'^\s*@router\.', ln) or re.match(r'^\s*(async\s+def|def)\s+\w+\s*\(', ln):
        ln_indent = re.match(r'^(\s*)', ln).group(1)
        if len(ln_indent) <= len(def_indent):
            end_off = cur
            break
    cur += len(ln)

before = s[:def_start]
after = tail[end_off:]

replacement = f"""{def_indent}def feed_live(
{def_indent}    limit: int = 50,
{def_indent}    since_id: int | None = None,
{def_indent}    threat_level: str | None = None,
{def_indent}    severity: str | None = None,
{def_indent}    only_actionable: bool = False,
{def_indent}    tenant_id: str | None = None,
{def_indent}):
{def_indent}    \"\"\"Live feed.
{def_indent}    NOTE: 'severity' is a query alias for threat_level (DB stores threat_level).
{def_indent}    \"\"\"
{def_indent}    # alias: severity -> threat_level (do not require schema changes)
{def_indent}    if (not threat_level) and severity:
{def_indent}        threat_level = severity

{def_indent}    # Local import to avoid circulars during early boot
{def_indent}    from api.db import SessionLocal, DecisionRecord

{def_indent}    with SessionLocal() as db:
{def_indent}        qry = db.query(DecisionRecord).order_by(DecisionRecord.id.desc())

{def_indent}        if since_id:
{def_indent}            qry = qry.filter(DecisionRecord.id > since_id)

{def_indent}        if tenant_id:
{def_indent}            qry = qry.filter(DecisionRecord.tenant_id == tenant_id)

{def_indent}        if threat_level:
{def_indent}            qry = qry.filter(DecisionRecord.threat_level == threat_level)

{def_indent}        if only_actionable:
{def_indent}            # Only apply if the column exists (schema-safe)
{def_indent}            if hasattr(DecisionRecord, "decision"):
{def_indent}                qry = qry.filter(DecisionRecord.decision.in_(["block", "challenge", "quarantine"]))

{def_indent}        rows = qry.limit(limit).all()

{def_indent}    items = []
{def_indent}    for r in rows:
{def_indent}        if hasattr(r, "to_dict"):
{def_indent}            d = r.to_dict()
{def_indent}        else:
{def_indent}            # best-effort fallback
{def_indent}            d = dict(getattr(r, "__dict__", {{}}))
{def_indent}            d.pop("_sa_instance_state", None)
{def_indent}        items.append(d)

{def_indent}    # Backfill UI-required fields so columns are real, not placeholders
{def_indent}    items = [_backfill_feed_item(i) for i in items]

{def_indent}    next_since_id = items[0].get("id") if items else since_id
{def_indent}    return {{"items": items, "next_since_id": next_since_id}}
"""

# Replace old def feed_live block only (keep decorator line(s) intact)
patched = before + replacement + after
p.write_text(patched, encoding="utf-8")

print("✅ feed_live overwritten safely")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
