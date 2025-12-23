#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"

echo "[*] Backing up api/defend.py..."
cp -a api/defend.py "api/defend.py.bak.${TS}"

echo "[*] Patching _to_utc() in api/defend.py to accept ISO strings..."

python - <<'PY'
from __future__ import annotations
from pathlib import Path
import re

p = Path("api/defend.py")
src = p.read_text(encoding="utf-8")

# Find the existing _to_utc definition block.
m = re.search(r"^def _to_utc\([^\n]*\):\n(?:^[ \t].*\n)+", src, flags=re.M)
if not m:
    raise SystemExit("ERROR: could not find def _to_utc(...) block in api/defend.py")

new_block = """def _to_utc(dt):
    \"""
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' (RFC3339) and naive datetimes.
    \"""
    from datetime import datetime, timezone

    if dt is None:
        return datetime.now(timezone.utc)

    # If already a datetime, normalize tz.
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # If it's a string, parse it.
    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            # Last-resort: treat unparseable strings as "now"
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    # Unknown type: best-effort fallback
    return datetime.now(timezone.utc)
"""

patched = src[:m.start()] + new_block + src[m.end():]
p.write_text(patched, encoding="utf-8")
print("[+] patched api/defend.py (_to_utc)")
PY

echo "[*] Quick compile..."
python -m py_compile api/defend.py api/schemas.py api/main.py

echo "[*] Re-running the failing doctrine test..."
pytest -q tests/test_doctrine.py::test_guardian_disruption_limit_and_roe_flags -q

echo "[✓] Timestamp patch applied."
