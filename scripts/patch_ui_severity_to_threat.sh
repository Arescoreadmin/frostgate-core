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

p = Path("api/ui.py")
s = p.read_text(encoding="utf-8")

# 1) Rename label in HTML (cosmetic)
s = s.replace(">Severity", ">Threat")

# 2) In getParams(), instead of p.set("severity", ...), map dropdown to threat_level
# Find the block that reads severity dropdown value.
# Replace:
#   const severity = document.getElementById("severity").value.trim();
# with:
#   const sev = document.getElementById("severity").value.trim();
s = re.sub(
    r'const\s+severity\s*=\s*document\.getElementById\("severity"\)\.value\.trim\(\);\s*',
    'const sev = document.getElementById("severity").value.trim();\n',
    s
)

# Replace any "if (severity) p.set("severity", severity);" with threat_level
s = re.sub(
    r'if\s*\(\s*severity\s*\)\s*p\.set\("severity",\s*severity\)\s*;\s*',
    'if (sev) p.set("threat_level", sev);\n',
    s
)

# Also handle if variable name changed already
s = re.sub(
    r'if\s*\(\s*sev\s*\)\s*p\.set\("severity",\s*sev\)\s*;\s*',
    'if (sev) p.set("threat_level", sev);\n',
    s
)

p.write_text(s, encoding="utf-8")
print("✅ patched ui: dropdown now filters threat_level")
PY

python -m py_compile api/ui.py
echo "✅ Compile OK: api/ui.py"
