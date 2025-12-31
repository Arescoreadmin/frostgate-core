#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

echo "==> Patch DefendResponse to include explanation_brief, and set it in defend() before return"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# --- ensure Optional is imported (if we need it) ---
if re.search(r'^from typing import .*$', s, flags=re.M) and "Optional" not in s:
    s = re.sub(
        r'^(from typing import [^\n]+)$',
        r'\1, Optional',
        s,
        count=1,
        flags=re.M
    )

# --- add explanation_brief to DefendResponse model if missing ---
m = re.search(r'(class\s+DefendResponse\s*\([^\)]*\)\s*:\s*\n)([\s\S]*?)(\nclass\s|\ndef\s|\nasync\s+def\s)', s)
if not m:
    raise SystemExit("❌ Could not locate DefendResponse class block")

hdr, body, tail = m.group(1), m.group(2), m.group(3)

if not re.search(r'^\s*explanation_brief\s*:', body, flags=re.M):
    # Insert near top of model fields: right after mitigations if present, else after threat_level
    insert_after = None
    for key in ("mitigations", "threat_level"):
        mm = re.search(rf'^(\s*{key}\s*:[^\n]*\n)', body, flags=re.M)
        if mm:
            insert_after = mm.end(1)
            break

    if insert_after is None:
        # Fallback: put it at the start of body
        insert_after = 0

    field_line = "    explanation_brief: str = \"\"\n"
    body = body[:insert_after] + field_line + body[insert_after:]

    s = s[:m.start()] + hdr + body + tail + s[m.end():]
    print("✅ Added explanation_brief to DefendResponse")
else:
    print("ℹ️ DefendResponse already has explanation_brief")

# --- ensure defend() sets explanation_brief before returning decision ---
# Find the `return decision` inside defend()
# We'll inject a small block immediately before it (only within defend()).
lines = s.splitlines(True)

# locate defend()
start = None
for i, ln in enumerate(lines):
    if re.match(r'^\s*async\s+def\s+defend\s*\(', ln) or re.match(r'^\s*def\s+defend\s*\(', ln):
        start = i
        break
if start is None:
    raise SystemExit("❌ Could not find defend() function")

# function end = next top-level def/class
end = len(lines)
for j in range(start+1, len(lines)):
    if re.match(r'^(def|async\s+def|class)\s+\w+', lines[j]):
        end = j
        break

block = lines[start:end]
block_text = "".join(block)

# If we already set explanation_brief near return decision, don't duplicate
if "FG_EXPLANATION_BRIEF_PATCH" in block_text:
    print("ℹ️ defend() already patched to set explanation_brief")
else:
    # find "return decision" line in defend()
    r_idx = None
    for k, ln in enumerate(block):
        if re.match(r'^\s*return\s+decision\s*$', ln):
            r_idx = k
            break
    if r_idx is None:
        raise SystemExit("❌ Could not find `return decision` inside defend()")

    indent = re.match(r'^(\s*)', block[r_idx]).group(1)

    patch = (
        f"{indent}# FG_EXPLANATION_BRIEF_PATCH\n"
        f"{indent}try:\n"
        f"{indent}    if not getattr(decision, 'explanation_brief', None):\n"
        f"{indent}        rule = 'rule:unknown'\n"
        f"{indent}        ex = getattr(decision, 'explain', None)\n"
        f"{indent}        if isinstance(ex, dict):\n"
        f"{indent}            rt = ex.get('rules_triggered') or []\n"
        f"{indent}            if rt:\n"
        f"{indent}                rule = rt[0]\n"
        f"{indent}        decision.explanation_brief = f\"Suspicious behavior matched rule '{rule}'.\"\n"
        f"{indent}except Exception:\n"
        f"{indent}    pass\n"
    )

    block.insert(r_idx, patch)
    lines[start:end] = block
    s = "".join(lines)
    print("✅ Injected explanation_brief setter before return decision")

p.write_text(s)
PY

python -m py_compile api/defend.py
echo "==> OK: api/defend.py compiles"
