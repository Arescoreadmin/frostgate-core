#!/usr/bin/env bash
set -euo pipefail

f="api/decisions.py"
if [[ ! -f "$f" ]]; then
  echo "❌ $f not found"
  exit 1
fi

echo "==> Fixing invalid escaped quotes in $f"
python - <<'PY'
from pathlib import Path

p = Path("api/decisions.py")
s = p.read_text()

if '\\"' not in s:
    print("ℹ️ No escaped quotes found in api/decisions.py. Nothing to do.")
    raise SystemExit(0)

s2 = s.replace('\\"', '"')
p.write_text(s2)
print('✅ Replaced \\\\" with " in api/decisions.py')
PY

echo "==> Quick syntax check"
.venv/bin/python -m py_compile api/decisions.py

echo "==> Run tests"
./scripts/test.sh
