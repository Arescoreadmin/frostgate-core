#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
lines = p.read_text().splitlines(True)

# 1) Find defend() definition line
def_i = None
for i, ln in enumerate(lines):
    if re.match(r'^\s*async\s+def\s+defend\s*\(', ln) or re.match(r'^\s*def\s+defend\s*\(', ln):
        def_i = i
        break
if def_i is None:
    raise SystemExit("❌ Could not find defend() function")

# 2) Determine body indent (first non-empty line after def)
body_indent = None
for j in range(def_i + 1, len(lines)):
    if lines[j].strip() == "":
        continue
    body_indent = re.match(r'^(\s*)', lines[j]).group(1)
    break
if body_indent is None or body_indent == "":
    raise SystemExit("❌ Could not determine defend() body indent")

# 3) Find the first `return decision` INSIDE defend() with that indent
ret_i = None
ret_pat = re.compile(r'^' + re.escape(body_indent) + r'return\s+decision\s*$')
for k in range(def_i + 1, len(lines)):
    # stop if we hit another top-level def/class at same or lower indent than body
    if k > def_i + 1 and re.match(r'^\S', lines[k]):  # new top-level statement
        # but defend() should not end before its return; if it does, bail
        pass
    if ret_pat.match(lines[k].rstrip("\n")):
        ret_i = k
        break

if ret_i is None:
    # fallback: any indented return decision
    for k in range(def_i + 1, len(lines)):
        if re.match(r'^\s+return\s+decision\s*$', lines[k]):
            ret_i = k
            body_indent = re.match(r'^(\s*)', lines[k]).group(1)
            break

if ret_i is None:
    raise SystemExit("❌ Could not find an indented 'return decision' inside defend()")

# 4) If we already set explanation_brief near return, do nothing
window = "".join(lines[max(def_i, ret_i-60):ret_i])
if "decision.explanation_brief" in window:
    print("ℹ️ explanation_brief already set near return; nothing to do")
    raise SystemExit(0)

# 5) Inject setter right before return
inject = [
    f"{body_indent}# Ensure top-level explanation_brief exists for MVP UX/tests\n",
    f"{body_indent}if getattr(decision, 'explanation_brief', None) in (None, ''):\n",
    f"{body_indent}    rt = None\n",
    f"{body_indent}    try:\n",
    f"{body_indent}        rt = getattr(decision, 'explain', {{}}).get('rules_triggered')\n",
    f"{body_indent}    except Exception:\n",
    f"{body_indent}        rt = None\n",
    f"{body_indent}    r0 = (rt[0] if isinstance(rt, list) and rt else None)\n",
    f"{body_indent}    decision.explanation_brief = (\n",
    f"{body_indent}        f\"Suspicious behavior matched rule '{'{'}r0 or 'rule:unknown'{'}'}'.\"\n",
    f"{body_indent}        if r0 else \"Decision computed.\"\n",
    f"{body_indent}    )\n",
]

lines[ret_i:ret_i] = inject
p.write_text("".join(lines))
print(f"✅ Injected explanation_brief setter before return at line {ret_i+1}")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
