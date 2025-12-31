#!/usr/bin/env bash
set -euo pipefail

f="api/main.py"
if [[ ! -f "$f" ]]; then
  echo "❌ api/main.py not found (run from repo root)"
  exit 1
fi

echo "==> [1/4] Ensure decisions router import exists"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text()

# If already imported, skip.
if re.search(r'from\s+api\.decisions\s+import\s+router\s+as\s+decisions_router', s):
    print("ℹ️ decisions_router import already present")
    raise SystemExit(0)

# Insert after other router imports (feed/stats/defend) if present, else near top.
lines = s.splitlines(True)

insert_at = None
for i, line in enumerate(lines):
    if re.search(r'from\s+api\.(feed|stats|defend)\s+import\s+router\s+as\s+\w+_router', line):
        insert_at = i + 1

if insert_at is None:
    # fallback: after standard imports block (first blank line after imports)
    for i, line in enumerate(lines):
        if line.strip() == "" and i > 5:
            insert_at = i + 1
            break
    insert_at = insert_at or 0

lines.insert(insert_at, "from api.decisions import router as decisions_router\n")
p.write_text("".join(lines))
print("✅ Added: from api.decisions import router as decisions_router")
PY

echo "==> [2/4] Mount decisions router in app wiring"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text()

# If already mounted, skip.
if re.search(r'include_router\(\s*decisions_router\s*\)', s):
    print("ℹ️ decisions_router already mounted")
    raise SystemExit(0)

# Find where other routers are mounted; place decisions next to feed/stats for customer surfaces.
m = re.search(r'(\n\s*app\.include_router\(\s*feed_router\s*\)\s*\n)', s)
if m:
    insert = m.group(1) + "    app.include_router(decisions_router)\n"
    s = s.replace(m.group(1), insert, 1)
else:
    # fallback: append near other include_router calls (after defend_router block if present)
    m2 = re.search(r'(\n\s*app\.include_router\(\s*stats_router\s*\)\s*\n)', s)
    if m2:
        s = s.replace(m2.group(1), m2.group(1) + "    app.include_router(decisions_router)\n", 1)
    else:
        # last resort: append at end of create_app wiring function
        s += "\n    app.include_router(decisions_router)\n"

p.write_text(s)
print("✅ Mounted: app.include_router(decisions_router)")
PY

echo "==> [3/4] Quick syntax check"
.venv/bin/python -m py_compile api/main.py

echo "==> [4/4] Run tests"
./scripts/test.sh
