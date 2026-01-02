#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/ui.py}"

if [[ ! -f "$FILE" ]]; then
  echo "ERROR: file not found: $FILE" >&2
  exit 1
fi

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
import re
from pathlib import Path

path = Path("api/ui.py")

s = path.read_text(encoding="utf-8")

orig = s

def must(pattern: str, msg: str):
    if re.search(pattern, s, flags=re.MULTILINE) is None:
        raise SystemExit(f"PATCH FAILED: {msg}")

# 1) Checkbox defaults: only_changed OFF, only_actionable ON, keep group_repeats ON
# Turn only_changed into unchecked
s = re.sub(
    r'(<input\s+id="only_changed"\s+type="checkbox")\s+checked(\s*>)',
    r'\1\2',
    s,
    flags=re.IGNORECASE
)

# Ensure only_changed exists; if it already lacked checked, ok.
must(r'id="only_changed"\s+type="checkbox"', "missing only_changed checkbox")

# Ensure only_actionable is checked
# If it exists without checked, add checked
s = re.sub(
    r'(<input\s+id="only_actionable"\s+type="checkbox")(?![^>]*\bchecked\b)([^>]*>)',
    r'\1 checked\2',
    s,
    flags=re.IGNORECASE
)
must(r'id="only_actionable"\s+type="checkbox"', "missing only_actionable checkbox")

# Ensure group_repeats is checked
s = re.sub(
    r'(<input\s+id="group_repeats"\s+type="checkbox")(?![^>]*\bchecked\b)([^>]*>)',
    r'\1 checked\2',
    s,
    flags=re.IGNORECASE
)
must(r'id="group_repeats"\s+type="checkbox"', "missing group_repeats checkbox")

# 2) Severity default: set medium as selected (only if it's present)
# Remove any existing selected attributes inside the severity select block, then set medium selected
sev_block = re.search(r'(<select\s+id="severity"[^>]*>.*?</select>)', s, flags=re.DOTALL | re.IGNORECASE)
if not sev_block:
    raise SystemExit("PATCH FAILED: missing severity <select id=\"severity\"> block")

block = sev_block.group(1)
block2 = re.sub(r'\s+selected\b', '', block, flags=re.IGNORECASE)
# set medium selected
block2 = re.sub(
    r'(<option\s+value="medium")(\s*>)',
    r'\1 selected\2',
    block2,
    flags=re.IGNORECASE
)
if 'value="medium"' not in block2:
    raise SystemExit("PATCH FAILED: severity select missing option value=\"medium\"")
s = s.replace(block, block2)

# 3) Fetch error: show response body text
# Replace the existing error handler that only shows status
# We look for the block:
# if (!res.ok) { status.textContent = `Error ${res.status}....`; return; }
# and replace with a hardened version that reads body.
s = re.sub(
    r'''
    if\s*\(\s*!\s*res\.ok\s*\)\s*\{
        \s*status\.textContent\s*=\s*`Error\s*\$\{res\.status\}[^`]*`;
        \s*return;
    \s*\}
    ''',
    r'''
  if (!res.ok) {
    const txt = await res.text();
    status.textContent = `Error ${res.status}: ${txt}`;
    return;
  }
    ''',
    s,
    flags=re.DOTALL | re.VERBOSE
)

# If that didn't match (because your string differs), do a weaker targeted patch:
if "const txt = await res.text();" not in s:
    # Find the first "if (!res.ok)" and inject body-read if it's a simple one-liner
    s = re.sub(
        r'if\s*\(\s*!\s*res\.ok\s*\)\s*\{\s*status\.textContent\s*=\s*`Error\s*\$\{res\.status\}[^`]*`;\s*return;\s*\}',
        r'if (!res.ok) { const txt = await res.text(); status.textContent = `Error ${res.status}: ${txt}`; return; }',
        s,
        flags=re.DOTALL
    )

# Validate that we now show body on errors
must(r'Error\s*\$\{res\.status\}:\s*\$\{txt\}', "failed to inject error body display")

# 4) Zero-items status banner
# After: const items = data.items || [];
# insert:
# if (items.length === 0) { status.textContent = `No results...`; }
if "No results (filters too strict?)" not in s:
    s = re.sub(
        r'(const\s+items\s*=\s*data\.items\s*\|\|\s*\[\]\s*;)',
        r'\1\n\n  if (items.length === 0) {\n    status.textContent = `No results (filters too strict?) lastId=${lastId}`;\n  }\n',
        s,
        count=1
    )

must(r'No results \(filters too strict\?\)', "failed to inject empty-results status message")

if s == orig:
    raise SystemExit("PATCH FAILED: no changes applied (file format drifted)")

path.write_text(s, encoding="utf-8")
print("Patched api/ui.py successfully.")
PY

python -m py_compile api/ui.py
echo "✅ Compile OK: api/ui.py"

echo
echo "Next: restart uvicorn and reload /ui/feed"
