#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/main.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text(encoding="utf-8")

# Replace the "resolved_auth_enabled = ..." line inside build_app with an inline, env-proof decision.
# This is the line that actually matters for tests and runtime.
pat = r"(def build_app\(auth_enabled: Optional\[bool\] = None\) -> FastAPI:\n(?:.*\n){0,20}?)(\s*resolved_auth_enabled\s*=\s*[^\n]+\n)"
m = re.search(pat, s, flags=re.M)
if not m:
    raise SystemExit("PATCH FAILED: couldn't locate resolved_auth_enabled assignment inside build_app()")

prefix = m.group(1)
replacement_line = (
    "    # Resolve ONCE. Explicit argument always wins over env.\n"
    "    resolved_auth_enabled = (auth_enabled if auth_enabled is not None else _resolve_auth_enabled_from_env())\n"
)

s2 = re.sub(pat, prefix + replacement_line, s, flags=re.M)

p.write_text(s2, encoding="utf-8")
print("✅ Patched build_app(): auth_enabled arg always wins over env")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK: $FILE"
