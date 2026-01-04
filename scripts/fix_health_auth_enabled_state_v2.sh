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

# --- 1) Insert: app.state.auth_enabled = bool(auth_enabled) right AFTER app = FastAPI(...)
m_build = re.search(r"(?m)^def\s+build_app\(", s)
if not m_build:
    raise SystemExit("PATCH FAILED: build_app not found")

# Limit search to build_app body-ish region for app = FastAPI(
build_region = s[m_build.start():]
m_app = re.search(r"(?m)^\s*app\s*=\s*FastAPI\(", build_region)
if not m_app:
    raise SystemExit("PATCH FAILED: app = FastAPI( not found inside build_app")

app_start = m_build.start() + m_app.start()
paren_start = s.find("FastAPI(", app_start)
i = paren_start + len("FastAPI(")
depth = 1
while i < len(s) and depth:
    ch = s[i]
    if ch == "(":
        depth += 1
    elif ch == ")":
        depth -= 1
    i += 1
if depth != 0:
    raise SystemExit("PATCH FAILED: could not find end of FastAPI(...) call")

# i is position just after the closing ')'
# insert after the end of that line
line_end = s.find("\n", i)
if line_end == -1:
    line_end = i

insert_line = "\n    app.state.auth_enabled = bool(auth_enabled)\n"
if "app.state.auth_enabled" not in s[m_build.start():line_end+500]:
    s = s[:line_end] + insert_line + s[line_end:]

# --- 2) Patch /health to report request.app.state.auth_enabled
# Ensure health takes Request if it doesn't already
if re.search(r'(?m)^@app\.get\("/health"\)', s):
    # add Request import if missing
    if "from fastapi import Request" not in s and "Request" not in s.splitlines()[0:40]:
        # try to add Request to an existing fastapi import line
        s = re.sub(r'(?m)^from fastapi import ([^\n]+)$',
                   lambda m: m.group(0) if "Request" in m.group(1) else f'from fastapi import {m.group(1)}, Request',
                   s, count=1)

    # ensure signature includes request: Request
    s = re.sub(r'(?m)^def\s+health\(\s*\):', 'def health(request: Request):', s, count=1)
    s = re.sub(r'(?m)^def\s+health\(\s*([^)]+)\):',
               lambda m: m.group(0) if "request" in m.group(1) else f'def health(request: Request, {m.group(1)}):',
               s, count=1)

    # replace auth_enabled value in returned dict if present
    # common: "auth_enabled": something
    s2, n = re.subn(r'("auth_enabled"\s*:\s*)([^,\n}]+)',
                    r'\1getattr(request.app.state, "auth_enabled", \2)',
                    s, count=1)
    s = s2

p.write_text(s)
print("✅ Patched build_app -> app.state.auth_enabled and /health reads app.state.auth_enabled")
PY

python -m py_compile api/main.py
echo "✅ Compile OK: api/main.py"
