#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
lines = p.read_text().splitlines(True)

# Find diff_basis start line
start = None
for i, line in enumerate(lines):
    if re.match(r'^\s*diff_basis\s*=\s*{', line):
        start = i
        break

if start is None:
    print("ℹ️ No diff_basis block found. Nothing to fix.")
    raise SystemExit(0)

# Find previous meaningful line (skip blanks + comments)
prev = start - 1
while prev >= 0:
    t = lines[prev].strip()
    if t == "" or t.startswith("#"):
        prev -= 1
        continue
    break

if prev < 0:
    raise SystemExit("❌ Could not locate a previous meaningful line before diff_basis.")

prev_line = lines[prev].rstrip("\n")
prev_indent = re.match(r'^(\s*)', lines[prev]).group(1)

# If previous line ends with ":" then an indent increase is valid.
# But you're getting "unexpected indent", so we force diff_basis to match prev indent exactly.
target_indent = prev_indent

def reindent(line: str, indent: str) -> str:
    return indent + line.lstrip()

# Reindent diff_basis dict block
out = lines[:start]
i = start

out.append(reindent(lines[i], target_indent))
i += 1

brace_depth = 1
while i < len(lines):
    line = lines[i]
    brace_depth += line.count("{")
    brace_depth -= line.count("}")
    out.append(reindent(line, target_indent))
    i += 1
    if brace_depth <= 0:
        break

out.extend(lines[i:])
p.write_text("".join(out))

print(f"✅ Reindented diff_basis block to match previous line indent at {prev+1}.")
print(f"   Previous line was: {prev_line}")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
