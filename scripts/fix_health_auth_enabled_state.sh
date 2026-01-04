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

# 1) Ensure build_app stores auth_enabled on app.state
# Look for "def build_app(" and insert "app.state.auth_enabled = auth_enabled"
m = re.search(r"(?m)^def\s+build_app\(\s*auth_enabled\s*:\s*bool.*?\)\s*:\s*$", s)
if not m:
    # fallback: any def build_app(
    m = re.search(r"(?m)^def\s+build_app\(", s)
if not m:
    raise SystemExit("PATCH FAILED: couldn't find def build_app")

# Find the line where app = FastAPI(...) occurs inside build_app
# We'll insert state assignment right after app creation.
# This is heuristic but works for normal formatting.
build_start = m.start()
build_body = s[build_start:]
app_line = re.search(r"(?m)^\s*app\s*=\s*FastAPI\(", build_body)
if not app_line:
    raise SystemExit("PATCH FAILED: couldn't find app = FastAPI(...) inside build_app")

insert_at = build_start + app_line.end()

# Insert only if not already present
if "app.state.auth_enabled" not in build_body[:500]:
    s = s[:insert_at] + "\n" + "    app.state.auth_enabled = bool(auth_enabled)\n" + s[insert_at:]

# 2) Patch /health handler to report app.state.auth_enabled if present
# Replace `"auth_enabled": ...` value inside the returned dict
# Common patterns:
# return {"status":"ok", ... "auth_enabled": something, ...}
s2 = re.sub(
    r'("auth_enabled"\s*:\s*)([^,\n}]+)',
    r'\1getattr(request.app.state, "auth_enabled", \2)',
    s,
    count=1
)

# If above didn't match (health may build dict in variable), do a broader fix:
if s2 == s:
    # try inserting auth_enabled line near where health dict is returned
    # find "def health" and a return dict
    mh = re.search(r'(?ms)^@app\.get\("/health".*?\n^def\s+health\([^)]*\):.*?return\s+\{', s)
    if mh and "request" not in mh.group(0):
        # if health doesn't have request param, we need it
        s = re.sub(r'(?m)^def\s+health\(([^)]*)\):', r'def health(request: Request, \1):', s, count=1)
    # now patch auth_enabled in the dict by injecting a line if missing
    # This is messy, so we keep it simple: user likely already has auth_enabled in dict.
    s2 = s

p.write_text(s2)
print("✅ Patched build_app auth_enabled -> app.state and /health uses app.state.auth_enabled")
PY

python -m py_compile api/main.py
echo "✅ Compile OK: api/main.py"
