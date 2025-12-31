#!/usr/bin/env bash
set -euo pipefail

f="Makefile"
test -f "$f" || { echo "❌ Makefile not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("Makefile")
lines = p.read_text().splitlines()

out = []
skip = False

for i, line in enumerate(lines):
    # Drop any malformed Kill-uvicorn / pattern variants
    if re.match(r'^(Kill-uvicorn|%kill-uvicorn|kill-uvicorn\s+[^:]+):', line):
        skip = True
        continue
    if skip:
        if line.startswith("\t"):
            continue
        skip = False

    out.append(line)

# Append clean canonical target at end
out += [
    "",
    "kill-uvicorn:",
    "\t-@echo \"Killing stray uvicorn processes…\"",
    "\t-sudo pkill -f \"uvicorn app.main:app\" || true",
    "\t-pkill -f \"uvicorn api.main:app\" || true",
    "\t-pkill -f \"python -m uvicorn api.main:app\" || true",
    "\t-pkill -f \".venv/bin/uvicorn api.main:app\" || true",
    "\t-@lsof -iTCP:8000 -sTCP:LISTEN -nP || true",
    "\t-@lsof -iTCP:8080 -sTCP:LISTEN -nP || true",
]

p.write_text("\n".join(out) + "\n")
print("✅ Normalized kill-uvicorn target")
PY
