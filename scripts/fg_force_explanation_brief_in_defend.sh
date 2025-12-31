#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

echo "==> Forcing /defend to always include top-level explanation_brief"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# --- 1) Ensure DefendResponse model has explanation_brief ---
# We add it if missing, without guessing too much about your existing model layout.
m = re.search(r'(class\s+DefendResponse\s*\([^\)]*\)\s*:\s*\n)(.*?)(\n\s*(?:async\s+def|def)\s+defend\s*\()', s, flags=re.S)
if not m:
    raise SystemExit("❌ Could not locate DefendResponse class block cleanly.")

hdr, body, tail = m.group(1), m.group(2), m.group(3)

if re.search(r'^\s*explanation_brief\s*:', body, flags=re.M) is None:
    # Insert near the top of the model body (after first line) to keep it visible.
    body_lines = body.splitlines(True)
    insert_at = 0
    # skip docstring if present
    if body_lines and re.match(r'^\s*(\"\"\"|\'\'\')', body_lines[0]):
        # find end of docstring
        for i in range(1, len(body_lines)):
            if re.search(r'(\"\"\"|\'\'\')\s*$', body_lines[i]):
                insert_at = i + 1
                break
    patch_line = "    explanation_brief: str | None = None\n"
    body_lines.insert(insert_at, patch_line)
    body = "".join(body_lines)
    s = s[:m.start(2)] + body + s[m.end(2):]
    print("✅ Added explanation_brief to DefendResponse")
else:
    print("ℹ️ DefendResponse already has explanation_brief")

# --- 2) Ensure defend() sets it before returning ---
# Strategy:
#   A) Try to compute explanation_brief right after rules_triggered is created.
#   B) Ensure it's present on the returned object (dict or pydantic model) right before "return decision".
#
# A) Insert computation after evaluation assignment (best-effort).
assign_pat = r'^(?P<indent>\s*)(?P<lhs>threat_level\s*,\s*rules_triggered\s*,\s*mitigations\s*,\s*anomaly_score\s*,\s*score)\s*=\s*.+$'
mm = re.search(assign_pat, s, flags=re.M)
if mm and "explanation_brief =" not in s[mm.end():mm.end()+300]:
    indent = mm.group("indent")
    insert = (
        f"\n{indent}# concise, customer-facing explanation\n"
        f"{indent}rule0 = (rules_triggered[0] if isinstance(rules_triggered, list) and rules_triggered else None)\n"
        f"{indent}explanation_brief = f\"Suspicious behavior matched rule '{'{'}rule0 or 'rule:unknown'{'}'}'.\" if rule0 else \"Decision computed.\"\n"
    )
    s = s[:mm.end()] + insert + s[mm.end():]
    print("✅ Inserted explanation_brief computation after rule evaluation")
else:
    print("ℹ️ Could not find rule evaluation assignment (or explanation_brief already computed). Will enforce at return.")

# B) Enforce presence right before return decision
# Find the exact "return decision" inside defend(). Use a conservative approach:
# locate defend() block, then patch the last "return decision" within that block.
dm = re.search(r'^\s*async\s+def\s+defend\s*\(.*?\)\s*->\s*DefendResponse\s*:\s*$', s, flags=re.M)
if not dm:
    # fallback: any defend() signature
    dm = re.search(r'^\s*(async\s+def|def)\s+defend\s*\(', s, flags=re.M)
if not dm:
    raise SystemExit("❌ Could not locate defend() function signature.")

# Find return decision after defend() starts
start = dm.start()
ret = None
for mret in re.finditer(r'^\s*return\s+decision\s*$', s[start:], flags=re.M):
    ret = mret
if not ret:
    raise SystemExit("❌ Could not find 'return decision' in defend().")

ret_abs = start + ret.start()
# determine indent at return line
ret_line_start = s.rfind("\n", 0, ret_abs) + 1
ret_indent = re.match(r'^(\s*)', s[ret_line_start:ret_abs]).group(1)

enforcer = (
    f"{ret_indent}# Ensure top-level explanation_brief is always present\n"
    f"{ret_indent}try:\n"
    f"{ret_indent}    _eb = locals().get('explanation_brief')\n"
    f"{ret_indent}    if not _eb:\n"
    f"{ret_indent}        _rt = locals().get('rules_triggered')\n"
    f"{ret_indent}        _r0 = (_rt[0] if isinstance(_rt, list) and _rt else None)\n"
    f"{ret_indent}        _eb = f\"Suspicious behavior matched rule '{'{'}_r0 or 'rule:unknown'{'}'}'.\" if _r0 else \"Decision computed.\"\n"
    f"{ret_indent}    if isinstance(decision, dict):\n"
    f"{ret_indent}        decision.setdefault('explanation_brief', _eb)\n"
    f"{ret_indent}    else:\n"
    f"{ret_indent}        setattr(decision, 'explanation_brief', _eb)\n"
    f"{ret_indent}except Exception:\n"
    f"{ret_indent}    pass\n"
)

# Insert enforcer immediately before return decision line
s = s[:ret_line_start] + enforcer + s[ret_line_start:]
p.write_text(s)
print("✅ Enforced explanation_brief right before return decision")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
