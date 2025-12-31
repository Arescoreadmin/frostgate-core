#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

echo "==> Patching api/defend.py to ensure /defend returns top-level explanation_brief"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
lines = p.read_text().splitlines(True)

# Locate defend() function block (sync or async)
start = None
for i, ln in enumerate(lines):
    if re.match(r'^\s*(async\s+def|def)\s+defend\s*\(', ln):
        start = i
        break
if start is None:
    raise SystemExit("❌ Could not find defend() in api/defend.py")

# Find end of function block by next top-level def/class (col 0)
end = len(lines)
for j in range(start + 1, len(lines)):
    if re.match(r'^(def|async\s+def|class)\s+\w+', lines[j]):
        end = j
        break

block = "".join(lines[start:end])

# Only care if explanation_brief already exists INSIDE defend()
if re.search(r'["\']explanation_brief["\']\s*:', block):
    print("ℹ️ defend() already returns explanation_brief (inside function). No change.")
    raise SystemExit(0)

# Find the first response dict key that looks like "explain": ...
# We insert explanation_brief right before it.
idx_in_block = None
block_lines = block.splitlines(True)
for k, ln in enumerate(block_lines):
    if re.search(r'["\']explain["\']\s*:', ln):
        idx_in_block = k
        break
if idx_in_block is None:
    raise SystemExit("❌ Could not find an 'explain' key inside defend() response to anchor insertion.")

indent = re.match(r'^(\s*)', block_lines[idx_in_block]).group(1)

insertion = (
    f'{indent}"explanation_brief": (\n'
    f"{indent}    f\"Suspicious behavior matched rule '{{(explain.get('rules_triggered') or ['rule:unknown'])[0]}}'.\"\n"
    f"{indent}    if isinstance(explain, dict) else \"Decision computed.\"\n"
    f"{indent}),\n"
)

block_lines.insert(idx_in_block, insertion)
new_block = "".join(block_lines)

# Replace defend() block in full file
new_text = "".join(lines[:start]) + new_block + "".join(lines[end:])
p.write_text(new_text)

print("✅ Injected explanation_brief into defend() response")
PY

python -m py_compile api/defend.py
