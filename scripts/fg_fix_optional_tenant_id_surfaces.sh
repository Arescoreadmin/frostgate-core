#!/usr/bin/env bash
set -euo pipefail

echo "==> Patch api/decisions.py DecisionOut.tenant_id to Optional[str]"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/decisions.py")
s = p.read_text()

# Ensure Optional import exists
if "from typing import" in s and "Optional" not in s:
    s = re.sub(r'from typing import ([^\n]+)\n',
               lambda m: f'from typing import {m.group(1).rstrip()}, Optional\n',
               s, count=1)

# Patch tenant_id annotation inside DecisionOut
# Handles patterns like: tenant_id: str  OR tenant_id: str = Field(...)
s2, n = re.subn(
    r'(\btenant_id\s*:\s*)str(\b)',
    r'\1Optional[str]\2',
    s,
    count=1
)

if n == 0:
    # fallback: sometimes it's "tenant_id: str =" on same line
    s2, n = re.subn(
        r'(\btenant_id\s*:\s*)str(\s*=)',
        r'\1Optional[str]\2',
        s,
        count=1
    )

if n == 0:
    raise SystemExit("❌ Could not find tenant_id: str in api/decisions.py DecisionOut model")

p.write_text(s2)
print("✅ Patched api/decisions.py (DecisionOut.tenant_id is now Optional[str])")
PY

echo "==> Patch api/feed.py FeedItem.tenant_id to Optional[str] (if present)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

# Ensure Optional import exists
if "from typing import" in s and "Optional" not in s:
    s = re.sub(r'from typing import ([^\n]+)\n',
               lambda m: f'from typing import {m.group(1).rstrip()}, Optional\n',
               s, count=1)

# Only patch if feed item model has tenant_id: str
s2, n = re.subn(
    r'(\btenant_id\s*:\s*)str(\b)',
    r'\1Optional[str]\2',
    s,
    count=1
)

if n == 0:
    # no tenant_id or already optional; write back only if import changed
    p.write_text(s)
    print("ℹ️ api/feed.py had no tenant_id: str to patch (or already Optional)")
else:
    p.write_text(s2)
    print("✅ Patched api/feed.py (tenant_id is now Optional[str])")
PY

echo "==> Quick syntax check"
.venv/bin/python -m py_compile api/decisions.py api/feed.py

echo "==> Run tests"
./scripts/test.sh
