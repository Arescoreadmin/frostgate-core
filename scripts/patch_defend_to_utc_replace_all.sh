#!/usr/bin/env bash
set -euo pipefail
TS="$(date +%Y%m%d_%H%M%S)"
cp -a api/defend.py "api/defend.py.bak.${TS}" || true

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
src = p.read_text(encoding="utf-8")

tolerant = """
def _to_utc(dt):
    \"""
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' and naive datetimes.
    \"""
    from datetime import datetime, timezone

    if dt is None:
        return datetime.now(timezone.utc)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)
""".lstrip()

pattern = r"^def _to_utc\([^\n]*\):\n(?:^[ \t].*\n)+"
matches = list(re.finditer(pattern, src, flags=re.M))
if not matches:
    raise SystemExit("No _to_utc() definition found to replace.")

# Replace ALL occurrences
src2 = re.sub(pattern, tolerant + "\n", src, flags=re.M)
p.write_text(src2, encoding="utf-8")

print(f"Replaced {len(matches)} _to_utc() definition(s).")
PY

find . -type d -name "__pycache__" -prune -exec rm -rf {} + || true
rm -rf .pytest_cache || true
python -m py_compile api/defend.py
