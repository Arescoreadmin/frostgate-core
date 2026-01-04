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

# ensure Request import
if not re.search(r'(?m)^from fastapi import .*Request', s):
    # try add to existing fastapi import
    s2, n = re.subn(r'(?m)^from fastapi import ([^\n]+)$',
                    lambda m: m.group(0) if "Request" in m.group(1) else f'from fastapi import {m.group(1)}, Request',
                    s, count=1)
    s = s2 if n else ('from fastapi import Request\n' + s)

# In build_app, after app = FastAPI(...), set app.state.auth_enabled using param precedence
m_build = re.search(r'(?ms)^def\s+build_app\((.*?)\):', s)
if not m_build:
    raise SystemExit("PATCH FAILED: build_app(...) not found")

build_start = m_build.start()
# find 'app = FastAPI(' inside build_app
m_app = re.search(r'(?ms)^\s*app\s*=\s*FastAPI\(', s[build_start:])
if not m_app:
    raise SystemExit("PATCH FAILED: app = FastAPI( not found in build_app")

app_line_start = build_start + m_app.start()
paren_start = s.find("FastAPI(", app_line_start)
i = paren_start + len("FastAPI(")
depth = 1
while i < len(s) and depth:
    if s[i] == "(":
        depth += 1
    elif s[i] == ")":
        depth -= 1
    i += 1
if depth != 0:
    raise SystemExit("PATCH FAILED: could not find end of FastAPI(...) call")

line_end = s.find("\n", i)
if line_end == -1:
    line_end = i

# inject auth_enabled resolution if not already present
inject = """
    # auth_enabled precedence: explicit param wins; else env/default logic inside module
    app.state.auth_enabled = bool(auth_enabled) if auth_enabled is not None else bool(getattr(app.state, "auth_enabled", False))
"""
if "auth_enabled precedence" not in s:
    s = s[:line_end] + inject + s[line_end:]

# Make sure /health signature includes request: Request
s = re.sub(r'(?m)^def\s+health\(\s*\):', 'def health(request: Request):', s, count=1)

# Force /health JSON auth_enabled to use app.state.auth_enabled
# Replace first occurrence of "auth_enabled": <something>
s, n = re.subn(r'("auth_enabled"\s*:\s*)([^,\n}]+)',
               r'\1bool(getattr(request.app.state, "auth_enabled", False))',
               s, count=1)
if n == 0:
    print("WARN: could not rewrite auth_enabled in /health payload (pattern not found)")

p.write_text(s)
print("✅ Patched build_app param precedence + /health reads app.state.auth_enabled")
PY

python -m py_compile api/main.py
echo "✅ Compile OK: api/main.py"
