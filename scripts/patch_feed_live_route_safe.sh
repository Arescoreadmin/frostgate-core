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

# -------------------------------
# Find the /live handler function
# -------------------------------
# Match either:
#   @router.get("/live"...)
#   @router.get("/feed/live"...)
dec = re.search(r'(?m)^\s*@router\.get\(\s*["\'](/live|/feed/live)["\']', s)
if not dec:
    # some codebases do @router.get("/live") with prefix="/feed" in APIRouter
    # If the file uses APIRouter(prefix="/feed"), decorator is likely "/live"
    raise SystemExit("PATCH FAILED: could not find @router.get('/live' or '/feed/live') in api/feed.py")

# The def line should follow after optional decorator args lines
post = s[dec.end():]
mdef = re.search(r'(?m)^\s*(async\s+def|def)\s+(?P<name>\w+)\s*\(', post)
if not mdef:
    raise SystemExit("PATCH FAILED: could not find function definition after the /live route decorator")

fn_name = mdef.group("name")
fn_def_abs = dec.end() + mdef.start()

# Determine function block range: from def line to next top-level def (same or less indentation)
# Get indentation of def line
def_line = s[fn_def_abs:s.find("\n", fn_def_abs)]
def_indent = re.match(r'^(\s*)', def_line).group(1)

# Find end of function by locating next line that starts with indentation <= def_indent and begins with "def " or "async def"
rest = s[fn_def_abs:]
lines = rest.splitlines(True)

end_idx = len(rest)
if len(lines) > 1:
    # Skip first line (def ...) then scan
    cur_pos = 0
    cur_pos += len(lines[0])
    for ln in lines[1:]:
        # candidate top-level or same-level def
        if re.match(r'^\s*(async\s+def|def)\s+\w+\s*\(', ln):
            # if indent of this def <= current def_indent, we've hit next sibling
            ln_indent = re.match(r'^(\s*)', ln).group(1)
            if len(ln_indent) <= len(def_indent):
                end_idx = cur_pos
                break
        cur_pos += len(ln)

fn_block = rest[:end_idx]

# ---------------------------------------------------------
# Patch 1: remove any DecisionRecord.severity references
# ---------------------------------------------------------
fn_block_new, n_rm = re.subn(
    r'(?m)^\s*qry\s*=\s*qry\.filter\(\s*DecisionRecord\.severity\s*==\s*severity\s*\)\s*\n',
    "",
    fn_block
)

# ---------------------------------------------------------
# Patch 2: severity param becomes an alias for threat_level
# ---------------------------------------------------------
# We insert right after the first "qry =" assignment inside this function.
m_qry = re.search(r'(?m)^(?P<indent>\s*)qry\s*=\s*.*$', fn_block_new)
if not m_qry:
    raise SystemExit("PATCH FAILED: could not find 'qry =' inside the /live handler (unexpected shape)")

indent = m_qry.group("indent")
insert_at = m_qry.end()

alias_block = f"""
{indent}# UI "severity" is an alias for DB threat_level (DB has no DecisionRecord.severity).
{indent}sev = (severity or "").strip().lower() if "severity" in locals() or "severity" in fn_locals() else ""
{indent}if sev:
{indent}    if sev in {{"critical","high","medium","low"}}:
{indent}        qry = qry.filter(DecisionRecord.threat_level == sev)
{indent}    elif sev == "info":
{indent}        qry = qry.filter((DecisionRecord.threat_level == None) | (DecisionRecord.threat_level.in_(["none","info",""])))
"""

# The "fn_locals" trick: we inject a helper line if needed.
# But easiest: avoid NameError by only using severity if it's already a param.
# We'll patch severity access more safely below by detecting if function signature contains severity.

# Determine if function signature has a "severity" param
sig = s[fn_def_abs:s.find("):", fn_def_abs)+2]
has_sev_param = bool(re.search(r'\bseverity\b', sig))

if has_sev_param:
    alias_block = f"""
{indent}# UI "severity" is an alias for DB threat_level (DB has no DecisionRecord.severity).
{indent}sev = (severity or "").strip().lower()
{indent}if sev:
{indent}    if sev in {{"critical","high","medium","low"}}:
{indent}        qry = qry.filter(DecisionRecord.threat_level == sev)
{indent}    elif sev == "info":
{indent}        qry = qry.filter((DecisionRecord.threat_level == None) | (DecisionRecord.threat_level.in_(["none","info",""])))
"""
else:
    # If no severity param exists, do NOT inject alias filter (UI and API must match).
    alias_block = f"""
{indent}# NOTE: UI may send 'severity', but this handler signature has no severity param.
{indent}# Add it to the handler signature if you want the UI severity dropdown to filter server-side.
"""

# Only insert once
if "UI \"severity\" is an alias" not in fn_block_new and "NOTE: UI may send 'severity'" not in fn_block_new:
    fn_block_new = fn_block_new[:insert_at] + alias_block + fn_block_new[insert_at:]

# ---------------------------------------------------------
# Patch 3: backfill before returning JSON (so UI is useful)
# ---------------------------------------------------------
# We’ll backfill title/summary/timestamp/severity/action_taken if missing.
# If helper doesn't exist, inject it at module scope once.
if "_backfill_feed_item" not in s:
    # Add a simple helper near top (after imports)
    ins = re.search(r'(?m)^(from __future__.*\n)+', s)
    pos = ins.end() if ins else 0
    helper = """
def _derive_severity_from_threat(threat: str | None) -> str:
    t = (threat or "").strip().lower()
    if t in {"critical","high","medium","low"}:
        return t
    if t in {"none","info",""}:
        return "info"
    return "info"

def _backfill_feed_item(i: dict) -> dict:
    # Ensure fields the UI expects are present and usable.
    i = dict(i or {})
    tl = i.get("threat_level")
    i.setdefault("severity", _derive_severity_from_threat(tl))
    i.setdefault("timestamp", i.get("created_at") or i.get("ts") or i.get("time"))
    # Action/title/summary are currently null in dev emitter. Backfill to something stable.
    i.setdefault("action_taken", i.get("decision") or i.get("action") or "log_only")
    et = i.get("event_type") or ""
    src = i.get("source") or ""
    i.setdefault("title", i.get("title") or f"{et or 'event'} from {src or 'unknown'}")
    # Summary should be short and scannable
    if not i.get("summary"):
        reason = i.get("action_reason") or ""
        i["summary"] = reason[:160] if reason else f"threat={tl or 'unknown'} action={i.get('action_taken')}"
    return i
"""
    s = s[:pos] + helper + s[pos:]

# Now inject backfill on the items list right before returning.
# Look for a return dict that includes '"items": items'
m_ret = re.search(r'(?m)^(?P<rindent>\s*)return\s+\{\s*["\']items["\']\s*:\s*items\b', fn_block_new)
if not m_ret:
    # Alternative: return FeedResponse(items=items, ...)
    m_ret = re.search(r'(?m)^(?P<rindent>\s*)return\s+.*\bitems\s*=\s*items\b', fn_block_new)

if m_ret:
    rindent = m_ret.group("rindent")
    backfill_line = f"{rindent}items = [_backfill_feed_item(i) for i in items]\n"
    # only add once
    pre = fn_block_new[:m_ret.start()]
    if "items = [_backfill_feed_item(i) for i in items]" not in pre[-500:]:
        fn_block_new = fn_block_new[:m_ret.start()] + backfill_line + fn_block_new[m_ret.start():]
else:
    raise SystemExit("PATCH FAILED: could not find a return statement to hook backfill before")

# Replace function block in file
s = s[:fn_def_abs] + fn_block_new + s[fn_def_abs+end_idx:]

p.write_text(s, encoding="utf-8")
print(f"✅ Patched /live handler: {fn_name} (removed DecisionRecord.severity filters: {n_rm})")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
