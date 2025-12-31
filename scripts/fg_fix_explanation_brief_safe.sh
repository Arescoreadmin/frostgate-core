#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# --- A) Ensure DefendResponse has explanation_brief ---
m = re.search(r"(class\s+DefendResponse\s*\([^\)]*\)\s*:\s*\n)(.*?)(\n\s*(?:async\s+def|def)\s+defend\s*\()", s, flags=re.S)
if not m:
    raise SystemExit("❌ Could not locate DefendResponse block")

hdr, body, tail = m.group(1), m.group(2), m.group(3)

if re.search(r"^\s*explanation_brief\s*:", body, flags=re.M) is None:
    # Insert near top of model body (after optional docstring)
    lines = body.splitlines(True)
    insert_at = 0
    if lines and re.match(r'^\s*(\"\"\"|\'\'\')', lines[0]):
        for i in range(1, len(lines)):
            if re.search(r'(\"\"\"|\'\'\')\s*$', lines[i]):
                insert_at = i + 1
                break
    lines.insert(insert_at, "    explanation_brief: str | None = None\n")
    body2 = "".join(lines)
    s = s[:m.start(2)] + body2 + s[m.end(2):]
    print("✅ Added explanation_brief to DefendResponse")
else:
    print("ℹ️ DefendResponse already has explanation_brief")

# --- B) Ensure defend() sets explanation_brief before returning ---
# Find defend() start
dm = re.search(r"^\s*async\s+def\s+defend\s*\(.*\)\s*->\s*DefendResponse\s*:\s*$", s, flags=re.M)
if not dm:
    dm = re.search(r"^\s*(async\s+def|def)\s+defend\s*\(", s, flags=re.M)
if not dm:
    raise SystemExit("❌ Could not locate defend()")

start = dm.start()

# Find the *last* "return decision" after defend start
rets = list(re.finditer(r"^\s*return\s+decision\s*$", s[start:], flags=re.M))
if not rets:
    raise SystemExit("❌ Could not find 'return decision' inside defend()")
ret = rets[-1]
ret_abs = start + ret.start()

# Determine indentation of the return line
line_start = s.rfind("\n", 0, ret_abs) + 1
indent = re.match(r"^(\s*)", s[line_start:ret_abs]).group(1)
if indent == "":
    raise SystemExit("❌ Safety stop: return decision appears unindented (would patch at top-level).")

# Only insert if not already enforced nearby
window = s[line_start-500:line_start+50]
if "decision.explanation_brief" in window or "setattr(decision, 'explanation_brief'" in window:
    print("ℹ️ explanation_brief already set near return; skipping inject")
else:
    inject = (
        f"{indent}# Ensure top-level explanation_brief exists for MVP UX\n"
        f"{indent}if getattr(decision, 'explanation_brief', None) in (None, ''):\n"
        f"{indent}    rt = getattr(decision, 'explain', {{}}).get('rules_triggered') if hasattr(decision, 'explain') else None\n"
        f"{indent}    r0 = (rt[0] if isinstance(rt, list) and rt else None)\n"
        f"{indent}    decision.explanation_brief = f\"Suspicious behavior matched rule '{'{'}r0 or 'rule:unknown'{'}'}'.\" if r0 else \"Decision computed.\"\n"
    )
    s = s[:line_start] + inject + s[line_start:]
    print("✅ Injected explanation_brief setter right before return decision")

p.write_text(s)
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
