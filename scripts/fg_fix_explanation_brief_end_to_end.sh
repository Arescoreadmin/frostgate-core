#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# --- 1) Ensure DefendResponse has explanation_brief ---
# Find DefendResponse class block
m = re.search(
    r"(class\s+DefendResponse\s*\([^\)]*\)\s*:\s*\n)(.*?)(\n\s*(?:async\s+def|def)\s+defend\s*\()",
    s,
    flags=re.S
)
if not m:
    raise SystemExit("❌ Could not locate DefendResponse block")

hdr, body, tail = m.group(1), m.group(2), m.group(3)

if re.search(r"^\s*explanation_brief\s*:", body, flags=re.M) is None:
    # Insert after possible docstring (first triple-quoted string)
    insert_at = 0
    doc = re.match(r'(\s*"""[\s\S]*?"""\s*\n)', body)
    if doc:
        insert_at = doc.end(1)
    new_body = body[:insert_at] + "    explanation_brief: str | None = None\n" + body[insert_at:]
    s = s[:m.start(2)] + new_body + s[m.end(2):]
    print("✅ Added explanation_brief to DefendResponse")
else:
    print("ℹ️ DefendResponse already has explanation_brief")

# --- 2) Ensure defend() returns it (build a new object rather than setattr) ---
# Find defend() and its "return decision" inside function.
lines = s.splitlines(True)

def_i = None
for i, ln in enumerate(lines):
    if re.match(r'^\s*async\s+def\s+defend\s*\(', ln) or re.match(r'^\s*def\s+defend\s*\(', ln):
        def_i = i
        break
if def_i is None:
    raise SystemExit("❌ Could not find defend() function")

# determine body indent
body_indent = None
for j in range(def_i + 1, len(lines)):
    if lines[j].strip() == "":
        continue
    body_indent = re.match(r'^(\s*)', lines[j]).group(1)
    break
if not body_indent:
    raise SystemExit("❌ Could not determine defend() body indent")

# find indented return decision
ret_i = None
ret_pat = re.compile(r'^' + re.escape(body_indent) + r'return\s+decision\s*$')
for k in range(def_i + 1, len(lines)):
    if ret_pat.match(lines[k].rstrip("\n")):
        ret_i = k
        break
if ret_i is None:
    raise SystemExit("❌ Could not find 'return decision' inside defend()")

# If already patched, skip
window = "".join(lines[max(def_i, ret_i-80):ret_i+1])
if "explanation_brief" in window and "DefendResponse(" in window:
    print("ℹ️ defend() already appears to build explanation_brief; skipping return patch")
    Path("api/defend.py").write_text("".join(lines))
    raise SystemExit(0)

# Inject block right before return decision:
# compute brief, then return a copy via model_copy(update=...)
inject = [
    f"{body_indent}# Ensure top-level explanation_brief is always present (tests + UX)\n",
    f"{body_indent}rt = None\n",
    f"{body_indent}try:\n",
    f"{body_indent}    rt = getattr(decision, 'explain', {{}}).get('rules_triggered')\n",
    f"{body_indent}except Exception:\n",
    f"{body_indent}    rt = None\n",
    f"{body_indent}r0 = (rt[0] if isinstance(rt, list) and rt else None)\n",
    f"{body_indent}brief = (f\"Suspicious behavior matched rule '{'{'}r0 or 'rule:unknown'{'}'}'.\" if r0 else \"Decision computed.\")\n",
    f"{body_indent}# Use model_copy to avoid setattr issues under pydantic v2\n",
    f"{body_indent}return decision.model_copy(update={{'explanation_brief': brief}})\n",
]

# Replace the single 'return decision' line with our injected return
lines[ret_i:ret_i+1] = inject

p.write_text("".join(lines))
print(f"✅ Patched defend() to return explanation_brief (replaced return at line {ret_i+1})")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
