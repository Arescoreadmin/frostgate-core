#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
lines = p.read_text().splitlines(True)

# Find the indent we should use (match record_kwargs or rules_value line inside the try block)
target_indent = None
for i, line in enumerate(lines):
    if re.search(r'^\s*record_kwargs\s*:\s*dict\[str,\s*Any\]\s*=\s*{', line):
        target_indent = re.match(r'^(\s*)', line).group(1)
        break
    if re.search(r'^\s*rules_value\s*=\s*', line):
        target_indent = re.match(r'^(\s*)', line).group(1)
        break

if target_indent is None:
    raise SystemExit("❌ Could not infer indentation level (no record_kwargs/rules_value found).")

# Locate diff_basis start
start = None
for i, line in enumerate(lines):
    if re.search(r'^\s*diff_basis\s*=\s*{', line):
        start = i
        break

if start is None:
    print("ℹ️ No diff_basis block found. Nothing to fix.")
    raise SystemExit(0)

# Reindent diff_basis block until the matching closing "}" line
# We'll treat the block as contiguous until a line that starts at same/less indent and isn't blank/comment,
# OR until we see a standalone closing brace that ends the dict.
block_indent = re.match(r'^(\s*)', lines[start]).group(1)

out = lines[:start]
i = start

# Helper: reindent a line by stripping leading whitespace then prefixing target
def reindent(line: str, indent: str) -> str:
    return indent + line.lstrip()

# Reindent first line
out.append(reindent(lines[i], target_indent))
i += 1

# Reindent subsequent lines that are part of the dict literal until we close the dict.
brace_depth = 1  # we've seen the opening "{"
while i < len(lines):
    line = lines[i]
    # Track braces roughly (good enough for dict literal formatting)
    brace_depth += line.count("{")
    brace_depth -= line.count("}")
    out.append(reindent(line, target_indent))
    i += 1
    if brace_depth <= 0:
        break

# Append remainder
out.extend(lines[i:])

p.write_text("".join(out))
print("✅ Reindented diff_basis block to match surrounding try-body indentation.")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
