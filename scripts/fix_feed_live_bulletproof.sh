#!/usr/bin/env bash
set -euo pipefail

FILE="api/feed.py"

# 1) Restore the newest backup that compiles
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
  echo "ERROR: No compilable api/feed.py.bak.* found. You need to manually pick an older known-good version." >&2
  exit 1
fi

echo "✅ Restored from: $best"

# 2) Patch feed_live safely (route-based but we already know function name exists)
ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Sanity: ensure we have feed_live
m = re.search(r'(?m)^\s*def\s+feed_live\s*\(|^\s*async\s+def\s+feed_live\s*\(', s)
if not m:
    raise SystemExit("PATCH FAILED: feed_live not found in api/feed.py")

# Grab function block
start = m.start()
rest = s[start:]
lines = rest.splitlines(True)
def_line = lines[0]
def_indent = re.match(r'^(\s*)', def_line).group(1)

end = len(rest)
cur = len(lines[0])
for ln in lines[1:]:
    if re.match(r'^\s*(async\s+def|def)\s+\w+\s*\(', ln):
        ln_indent = re.match(r'^(\s*)', ln).group(1)
        if len(ln_indent) <= len(def_indent):
            end = cur
            break
    cur += len(ln)

block = rest[:end]

# --- A) Remove DecisionRecord.severity filters (they explode)
block, n_rm = re.subn(
    r'(?m)^\s*qry\s*=\s*qry\.filter\(\s*DecisionRecord\.severity\s*==\s*severity\s*\)\s*\n',
    "",
    block
)

# --- B) Ensure severity is an alias for threat_level
# Insert near top of function body (after first line that assigns/declares qry, or after first body line)
# We'll insert after the first "qry =" line if present, else after the first non-empty body line.
insert = None
m_qry = re.search(r'(?m)^(?P<i>\s*)qry\s*=\s*.*$', block)
if m_qry:
    insert = m_qry.end()
    indent = m_qry.group("i")
else:
    # find first indented line after def (function body)
    body_lines = block.splitlines(True)
    indent = None
    for i, ln in enumerate(body_lines[1:], start=1):
        if ln.strip():
            indent = re.match(r'^(\s*)', ln).group(1)
            insert = sum(len(x) for x in body_lines[:i+1])
            break
    if indent is None or insert is None:
        raise SystemExit("PATCH FAILED: couldn't determine insertion point inside feed_live")

alias_snippet = f"""
{indent}# 'severity' is a UI alias for threat_level (DB has threat_level only).
{indent}if (not threat_level) and severity:
{indent}    threat_level = severity
"""

if "UI alias for threat_level" not in block:
    block = block[:insert] + alias_snippet + block[insert:]

# --- C) Fix any dangling "if threat_level:" blocks (the thing currently killing uvicorn)
# Replace any empty if threat_level: (followed by blank/whitespace or comment-only) with a real filter
block = re.sub(
    r'(?ms)^(?P<i>\s*)if\s+threat_level\s*:\s*\n(?:(?P<i2>\s*)#.*\n|\s*\n)*',
    r'\g<i>if threat_level:\n\g<i>    qry = qry.filter(DecisionRecord.threat_level == threat_level)\n',
    block
)

# If there's no threat_level filter at all, add one after alias snippet (rare)
if "DecisionRecord.threat_level == threat_level" not in block:
    block = re.sub(
        r'(?m)^\s*# \'severity\' is a UI alias for threat_level.*\n(?:.*\n){0,3}',
        lambda m: m.group(0) + f"{indent}if threat_level:\n{indent}    qry = qry.filter(DecisionRecord.threat_level == threat_level)\n",
        block,
        count=1
    )

# --- D) Apply backfill before returning so UI columns are real, not cosplay.
# Insert before "return {"items": items, ...}" OR before "return FeedLiveResponse(...items=items...)"
def inject_backfill(text: str) -> str:
    # dict return
    mret = re.search(r'(?m)^(?P<ri>\s*)return\s+\{\s*["\']items["\']\s*:\s*items\b', text)
    if mret:
        ri = mret.group("ri")
        pre = text[:mret.start()]
        if "items = [_backfill_feed_item(i) for i in items]" not in pre[-600:]:
            text = text[:mret.start()] + f"{ri}items = [_backfill_feed_item(i) for i in items]\n" + text[mret.start():]
        return text

    # pydantic return
    mret = re.search(r'(?m)^(?P<ri>\s*)return\s+FeedLiveResponse\(', text)
    if mret and "items=" in text[mret.start():mret.start()+400]:
        ri = mret.group("ri")
        pre = text[:mret.start()]
        if "items = [_backfill_feed_item(i) for i in items]" not in pre[-600:]:
            text = text[:mret.start()] + f"{ri}items = [_backfill_feed_item(i) for i in items]\n" + text[mret.start():]
        return text

    raise SystemExit("PATCH FAILED: couldn't find return to hook backfill")

block = inject_backfill(block)

# Write back
s = s[:start] + block + s[start+end:]
p.write_text(s, encoding="utf-8")

print(f"✅ Patched feed_live safely. Removed DecisionRecord.severity filters: {n_rm}")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
