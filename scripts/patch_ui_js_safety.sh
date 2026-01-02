#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/ui.py}"
[[ -f "$FILE" ]] || { echo "Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

path = Path("api/ui.py")
s = path.read_text(encoding="utf-8")

# 1) Replace escapeHtml() with a clean implementation (avoid bad quoting issues)
escape_pat = re.compile(r"function\s+escapeHtml\s*\(\s*s\s*\)\s*\{.*?\}\n", re.DOTALL)
new_escape = """function escapeHtml(s){
  const str = String(s ?? "");
  return str.replace(/[&<>"']/g, (c) => ({
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#39;"
  }[c]));
}
"""

if not escape_pat.search(s):
    raise SystemExit("PATCH FAILED: escapeHtml() not found in api/ui.py HTML")
s = escape_pat.sub(new_escape + "\n", s, count=1)

# 2) Wrap loadIncremental in try/catch so failures show up in status instead of blank UI.
# Find "async function loadIncremental" and inject a try/catch wrapper inside.
load_pat = re.compile(r"(async function\s+loadIncremental\s*\([^)]*\)\s*\{\n)(.*?)(\n\}\n)", re.DOTALL)
m = load_pat.search(s)
if not m:
    raise SystemExit("PATCH FAILED: loadIncremental() not found")

head, body, tail = m.group(1), m.group(2), m.group(3)

# If already wrapped, don’t double-wrap.
if "try {" not in body:
    wrapped_body = f"""  const status = document.getElementById("status");
  try {{
{body.rstrip()}
  }} catch (e) {{
    console.error("UI loadIncremental error:", e);
    status.textContent = "UI error: " + (e && e.message ? e.message : String(e));
  }}
"""
    s = s[:m.start()] + head + wrapped_body + tail + s[m.end():]

path.write_text(s, encoding="utf-8")
print("✅ Patched escapeHtml() + loadIncremental() safety")
PY

python -m py_compile api/ui.py
echo "✅ Compile OK: api/ui.py"

echo
echo "=== Proof (served HTML) ==="
curl -sS http://127.0.0.1:8000/ui/feed | rg -n 'function escapeHtml|UI error:|async function loadIncremental' -n | sed -n '1,120p'
