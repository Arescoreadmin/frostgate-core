#!/usr/bin/env bash
set -euo pipefail

f="api/feed.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

echo "==> Patching api/feed.py to expose core decision fields on feed items"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

if "class FeedItem" not in s:
    raise SystemExit("❌ Could not find FeedItem model in api/feed.py")

# --- ensure Optional is imported ---
if re.search(r"from typing import .*Optional", s) is None:
    s = re.sub(
        r"from typing import ([^\n]+)\n",
        lambda m: f"from typing import {m.group(1).rstrip()}, Optional\n",
        s,
        count=1,
    )

# --- add fields to FeedItem model block ---
m = re.search(r"(class\s+FeedItem\(BaseModel\):\n)(.*?)(\nclass\s|\n@router|\Z)", s, re.S)
if not m:
    raise SystemExit("❌ Could not isolate FeedItem(BaseModel) block")

head, body, tail = m.group(1), m.group(2), m.group(3)

wanted = [
    "id: Optional[int] = None",
    "event_id: Optional[str] = None",
    "event_type: Optional[str] = None",
    "source: Optional[str] = None",
    "tenant_id: Optional[str] = None",
    "threat_level: Optional[str] = None",
]

existing_fields = set(re.findall(r"^\s*([a-zA-Z_]\w*)\s*:", body, re.M))

inserts = []
for line in wanted:
    key = line.split(":")[0].strip()
    if key not in existing_fields:
        inserts.append("    " + line)

if inserts:
    # insert right after class header (top of model fields)
    new_body = "\n".join(inserts) + ("\n" if body and not body.startswith("\n") else "") + body
else:
    new_body = body

s = s[:m.start()] + head + new_body + s[m.start()+len(head+body):]

# --- inject kwargs into FeedItem(...) construction ---
# We locate the FIRST FeedItem( ... timestamp=... ) in feed_live and ensure kwargs exist.
def inject_kwarg(block: str, kw: str, expr: str) -> str:
    if re.search(rf"\b{kw}\s*=", block):
        return block
    # insert before timestamp= if present, else before closing paren
    if "timestamp=" in block:
        return re.sub(r"(\n\s*timestamp\s*=)", f"\n        {kw}={expr},\\1", block, count=1)
    return re.sub(r"\n\s*\)\s*$", f"\n        {kw}={expr},\n    )", block, count=1)

# Find an items.append(FeedItem(...)) or return FeedItem(...)
fm = re.search(r"(FeedItem\(\s*\n.*?\n\s*\)\s*)", s, re.S)
if not fm:
    raise SystemExit("❌ Could not find a FeedItem(...) construction to patch")

feeditem_ctor = fm.group(1)

# Most code uses `r` as the DecisionRecord row. If yours uses something else, we keep getattr safe.
# We'll try to infer the row var by spotting "getattr(" usages in the ctor.
row_var = "r"
mvar = re.search(r"getattr\((\w+),\s*['\"]decision_diff_json['\"]", feeditem_ctor)
if mvar:
    row_var = mvar.group(1)

patches = [
    ("id", f"getattr({row_var}, 'id', None)"),
    ("event_id", f"getattr({row_var}, 'event_id', None)"),
    ("event_type", f"getattr({row_var}, 'event_type', None)"),
    ("source", f"getattr({row_var}, 'source', None)"),
    ("tenant_id", f"getattr({row_var}, 'tenant_id', None)"),
    ("threat_level", f"getattr({row_var}, 'threat_level', None)"),
]

for kw, expr in patches:
    feeditem_ctor = inject_kwarg(feeditem_ctor, kw, expr)

s = s[:fm.start()] + feeditem_ctor + s[fm.end():]

p.write_text(s)
print("✅ Patched api/feed.py (FeedItem fields + FeedItem(...) includes core decision fields)")
PY

python -m py_compile api/feed.py
echo "✅ api/feed.py compiles"
