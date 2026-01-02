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

# --- Fix Severity default ---
# 1) remove any existing 'selected' on severity options (critical/high/medium/low/info)
s = re.sub(r'(<option value="(?:critical|high|medium|low|info)")\s+selected', r"\1", s)

# 2) ensure the "any" option exists and is selected
# If it already exists, force it to selected. If not, insert it right after <select id="severity">
if re.search(r'<option value=""[^>]*>any</option>', s):
    s = re.sub(r'(<option value="")([^>]*)(>any</option>)', r'\1 selected\3', s)
else:
    s = s.replace(
        '<select id="severity">',
        '<select id="severity">\n        <option value="" selected>any</option>',
        1
    )

# --- Fix checkbox defaults ---
# ensure only_actionable is NOT checked by default
s = re.sub(
    r'(<input id="only_actionable"\s+type="checkbox")\s+checked(\s*>)',
    r"\1\2",
    s,
    flags=re.I
)

path.write_text(s, encoding="utf-8")
print("✅ Patched api/ui.py")
PY

python -m py_compile api/ui.py
echo "✅ Compile OK"

echo
echo "=== Proof from server-side file (api/ui.py) ==="
rg -n '<select id="severity">|option value="|only_actionable' -n api/ui.py | sed -n '1,120p'
