#!/usr/bin/env bash
set -euo pipefail

f="api/feed.py"
if [[ ! -f "$f" ]]; then
  echo "❌ $f not found"
  exit 1
fi

echo "==> Fixing invalid escaped quotes in $f"
python - <<'PY'
from pathlib import Path

p = Path("api/feed.py")
s = p.read_text()

# Only fix the specific bad pattern(s). If your file somehow contains \" elsewhere,
# it's not valid Python source anyway, so converting it is correct.
if '\\"' not in s:
    print("ℹ️ No escaped quotes found (\\\") in api/feed.py. Nothing to do.")
else:
    s2 = s.replace('\\"', '"')
    p.write_text(s2)
    print("✅ Replaced \\\\\" with \" in api/feed.py")
PY

echo "==> Quick syntax check"
python -m py_compile api/feed.py
echo "✅ api/feed.py compiles"

echo "==> Run tests"
./scripts/test.sh
