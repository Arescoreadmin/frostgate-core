#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-api/main.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from __future__ import annotations
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text()

# 1) Fix broken /health line if present (remove garbage tokens after the bool(...))
# Replace ANY line containing '"auth_enabled":' with a clean one (within health block).
s = re.sub(
    r'(?m)^\s*"auth_enabled"\s*:\s*.*$',
    '            "auth_enabled": bool(getattr(request.app.state, "auth_enabled", False)),',
    s
)

# 2) Ensure health signature has request: Request
s = re.sub(r'(?m)^(\s*)async def health\(\s*\)\s*->\s*dict\s*:',
           r'\1async def health(request: Request) -> dict:', s)
s = re.sub(r'(?m)^(\s*)async def health\(\s*\)\s*:',
           r'\1async def health(request: Request):', s)

# 3) In build_app, REMOVE any existing app.state.auth_enabled assignments (they’re fighting each other)
# Only within build_app block.
m = re.search(r'(?ms)^def build_app\([^\n]*\):\n(.*?)(?=^app\s*=\s*build_app\(|\Z)', s)
if not m:
    raise SystemExit("PATCH FAILED: build_app block not found")

block = m.group(0)
block_clean = re.sub(r'(?m)^\s*app\.state\.auth_enabled\s*=.*\n', '', block)

# 4) Find where app = FastAPI(...) is created, then insert ONE authoritative assignment right after.
# We use resolved_auth_enabled if present; else compute it.
if "resolved_auth_enabled" not in block_clean:
    # Insert a resolved_auth_enabled line near top of build_app body after doc/first comment.
    block_clean = re.sub(
        r'(?ms)^(def build_app\([^\n]*\):\n)',
        r'\1    # Resolve auth once: explicit param wins over env.\n'
        r'    resolved_auth_enabled = (auth_enabled if auth_enabled is not None else _resolve_auth_enabled_from_env())\n\n',
        block_clean,
        count=1
    )

# Insert the state assignment after FastAPI(...) line.
block_clean, n_ins = re.subn(
    r'(?m)^(\s*)app\s*=\s*FastAPI\([^\n]*\)\s*$',
    r'\g<0>\n\1# Authoritative auth flag (param overrides env).\n\1app.state.auth_enabled = bool(resolved_auth_enabled)',
    block_clean,
    count=1
)
if n_ins == 0:
    raise SystemExit("PATCH FAILED: could not find app = FastAPI(...) line inside build_app")

# 5) Replace the old block with cleaned block
s = s[:m.start()] + block_clean + s[m.end():]

p.write_text(s)
print("✅ Patched build_app auth_enabled + fixed /health auth_enabled line.")
PY

python -m py_compile api/main.py
echo "✅ Compile OK: api/main.py"
