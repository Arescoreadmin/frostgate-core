#!/usr/bin/env bash
set -euo pipefail
TS="$(date +%Y%m%d_%H%M%S)"
cp -a api/defend.py "api/defend.py.bak.${TS}" || true

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
src = p.read_text(encoding="utf-8")

# 1) Replace call sites first (safe even if already done)
src = re.sub(r"\b_to_utc\(", "_to_utc_any(", src)

# 2) Ensure helper exists near top (after imports)
helper = r'''
def _to_utc_any(dt):
    """
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' and naive datetimes.
    """
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
'''.lstrip()

if "_to_utc_any" not in src:
    # insert after first import block
    m = re.search(r"^(?:from __future__.*\n)?(?:import .*?\n|from .*? import .*?\n)+\n", src, flags=re.M)
    if m:
        src = src[:m.end()] + helper + "\n" + src[m.end():]
    else:
        src = helper + "\n" + src

p.write_text(src, encoding="utf-8")
print("Patched api/defend.py: call sites -> _to_utc_any(), helper injected.")
PY

find . -type d -name "__pycache__" -prune -exec rm -rf {} + || true
rm -rf .pytest_cache || true
python -m py_compile api/defend.py
