#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# Replace signature + add None guard if not already present
sig_pat = r"def\s+_to_utc\(\s*dt:\s*datetime\s*\|\s*str\s*\)\s*->\s*datetime\s*:"
if re.search(sig_pat, s):
    s = re.sub(sig_pat, "def _to_utc(dt: datetime | str | None) -> datetime:", s)

# Inject None guard right after function line if missing
m = re.search(r"def _to_utc\(dt: datetime \| str \| None\) -> datetime:\n", s)
if not m:
    raise SystemExit("❌ Could not find _to_utc after signature patch")

start = m.end()
if "if dt is None:" not in s[start:start+200]:
    s = s[:start] + "    if dt is None:\n        return datetime.now(timezone.utc)\n" + s[start:]

p.write_text(s)
print("✅ Patched _to_utc(None) -> now(UTC)")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
