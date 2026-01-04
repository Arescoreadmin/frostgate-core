#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-api/feed.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Ensure helper exists
if "_backfill_feed_item" not in s:
    raise SystemExit("ERROR: _backfill_feed_item helper not found in api/feed.py. Re-run your helper injection or paste it in.")

# Replace return {"items": items, ...} to apply backfill
pat = r"return\s+\{\s*([\"']items[\"']\s*:\s*items\s*,\s*[\"']next_since_id[\"']\s*:\s*[^}]+)\}"
if re.search(pat, s) and "items = [_backfill_feed_item(i) for i in items]" not in s:
    s = re.sub(
        pat,
        "items = [_backfill_feed_item(i) for i in items]\n    return {\\1}",
        s,
        count=1
    )
else:
    # fallback: insert before any 'return {"items":'
    s = re.sub(r"\n(\s*)return\s+\{\s*[\"']items[\"']\s*:\s*items\s*,",
               r"\n\1items = [_backfill_feed_item(i) for i in items]\n\1return {\"items\": items,",
               s,
               count=1)

p.write_text(s, encoding="utf-8")
print("✅ Patched: apply _backfill_feed_item() to items right before returning.")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
