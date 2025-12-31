#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# Replace signature to accept None
s = re.sub(
    r"def\s+_to_utc\(\s*dt:\s*datetime\s*\|\s*str\s*\)\s*->\s*datetime\s*:",
    "def _to_utc(dt: datetime | str | None) -> datetime:",
    s,
)

# Insert None guard immediately after function signature if not present
pat = r"(def _to_utc\(dt: datetime \| str \| None\) -> datetime:\n)"
m = re.search(pat, s)
if not m:
    raise SystemExit("❌ Could not locate updated _to_utc signature (did it differ?)")

# If guard already exists, don't duplicate
after = s[m.end(1):m.end(1)+200]
if "if dt is None" not in after:
    s = s[:m.end(1)] + "    if dt is None:\n        return datetime.now(timezone.utc)\n" + s[m.end(1):]

p.write_text(s)
print("✅ Patched _to_utc to handle None")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
