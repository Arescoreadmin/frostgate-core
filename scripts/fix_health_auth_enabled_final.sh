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

# Ensure Request imported
if "Request" not in s:
    s = re.sub(r'(?m)^from fastapi import ([^\n]+)$',
               lambda m: m.group(0) if "Request" in m.group(1) else f"from fastapi import {m.group(1)}, Request",
               s, count=1)

# Ensure health(request: Request) signature
s = re.sub(r'(?m)^def\s+health\(\s*\):', 'def health(request: Request):', s, count=1)

# Force /health to read state (replace first auth_enabled field in returned dict)
s, n = re.subn(
    r'("auth_enabled"\s*:\s*)([^,\n}]+)',
    r'\1bool(getattr(request.app.state, "auth_enabled", False))',
    s,
    count=1
)
if n == 0:
    print("WARN: Could not rewrite auth_enabled in /health payload (pattern not found)")

# In build_app: set app.state.auth_enabled at the VERY END so nothing overrides it.
m_build = re.search(r'(?ms)^def\s+build_app\((.*?)\):', s)
if not m_build:
    raise SystemExit("PATCH FAILED: build_app(...) not found")

# Find the 'return app' in build_app block (last one)
# We'll inject immediately before it.
# This is intentionally dumb-and-effective: last assignment wins.
return_pat = r'(?m)^\s*return\s+app\s*$'
m_ret = list(re.finditer(return_pat, s))
if not m_ret:
    raise SystemExit("PATCH FAILED: return app not found")

# Insert before the last 'return app'
ret = m_ret[-1]
inject = """
    # FINAL AUTH FLAG: explicit param wins over env/defaults (and wins over any earlier assignment).
    if auth_enabled is not None:
        app.state.auth_enabled = bool(auth_enabled)
    else:
        app.state.auth_enabled = bool(getattr(app.state, "auth_enabled", False))
"""
s = s[:ret.start()] + inject + s[ret.start():]

p.write_text(s)
print("✅ Patched: /health reads app.state.auth_enabled; build_app param wins (last-write).")
PY

python -m py_compile api/main.py
echo "✅ Compile OK: api/main.py"
