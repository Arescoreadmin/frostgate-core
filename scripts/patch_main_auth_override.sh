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

# Force explicit param override to always win.
# Replace the entire _resolve_auth_override() implementation with a bulletproof version.
pattern = r"def _resolve_auth_override\(auth_enabled: Optional\[bool\]\) -> bool:\n(?:[ \t].*\n)+?\n"
replacement = (
    "def _resolve_auth_override(auth_enabled: Optional[bool]) -> bool:\n"
    "    \"\"\"Explicit arg wins, always.\n"
    "    Env is only used when auth_enabled is None.\n"
    "    \"\"\"\n"
    "    if auth_enabled is None:\n"
    "        return _resolve_auth_enabled_from_env()\n"
    "    return bool(auth_enabled)\n\n"
)

if re.search(pattern, s, flags=re.M) is None:
    raise SystemExit("PATCH FAILED: couldn't find _resolve_auth_override() to replace")

s2 = re.sub(pattern, replacement, s, flags=re.M)

# Safety: ensure build_app uses _resolve_auth_override(auth_enabled) and stores it once.
# If someone “optimized” it wrong, fix the line.
s2 = re.sub(
    r"resolved_auth_enabled\s*=\s*_resolve_auth_override\([^)]+\)",
    "resolved_auth_enabled = _resolve_auth_override(auth_enabled)",
    s2,
)

p.write_text(s2, encoding="utf-8")
print("✅ Patched api/main.py: explicit auth override always wins")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK: $FILE"
