#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
echo "[*] Patch timestamp: $TS"
test -f api/main.py && cp -a api/main.py "api/main.py.bak.${TS}" || true

python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
src = p.read_text(encoding="utf-8")

# Replace any AUTH_ENABLED assignment that uses bool(auth_enabled)
src2 = re.sub(
    r"AUTH_ENABLED\s*=\s*bool\(\s*auth_enabled\s*\)",
    "AUTH_ENABLED = True if auth_enabled is True else False",
    src
)

# Also replace any app.state.auth_enabled = bool(auth_enabled) if present
src2 = re.sub(
    r"app\.state\.auth_enabled\s*=\s*bool\(\s*auth_enabled\s*\)",
    "app.state.auth_enabled = AUTH_ENABLED",
    src2
)

if src2 == src:
    raise SystemExit("No changes made. api/main.py didn't contain expected patterns.")

p.write_text(src2, encoding="utf-8")
print("Patched api/main.py")
PY

echo "[*] Compile..."
python -m py_compile api/main.py

echo "[*] Prove behavior in-process:"
python - <<'PY'
from api.main import build_app
a = build_app(False)
b = build_app(True)
print("build_app(False).state.auth_enabled =", a.state.auth_enabled)
print("build_app(True).state.auth_enabled  =", b.state.auth_enabled)
PY

echo "[*] Run the failing test..."
pytest -q tests/test_auth.py::test_health_reflects_auth_enabled\[False\] -q
