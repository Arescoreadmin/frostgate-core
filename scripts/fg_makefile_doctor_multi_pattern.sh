#!/usr/bin/env bash
set -euo pipefail

f="Makefile"
test -f "$f" || { echo "❌ Makefile not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("Makefile")
lines = p.read_text().splitlines(True)

def is_rule_header(line: str) -> bool:
    if line.startswith(("\t", "#")): return False
    if "=" in line and ":" not in line.split("=",1)[0]:  # variable assignment
        return False
    return ":" in line

bad_idxs = []
for i, line in enumerate(lines):
    if not is_rule_header(line): 
        continue
    # target section is before first ':'
    target = line.split(":", 1)[0]
    # multiple '%' anywhere in the *target list* is the classic failure
    if target.count("%") >= 2:
        bad_idxs.append(i)
    else:
        # also catch multiple pattern targets separated by spaces: "%.a %.b:"
        toks = target.split()
        if sum(1 for t in toks if "%" in t) >= 2:
            bad_idxs.append(i)

if not bad_idxs:
    print("✅ No multi-pattern target headers found. The issue is something else near the reported line.")
    raise SystemExit(0)

print("🚨 Found multi-pattern rule headers (these break GNU make):")
for idx in bad_idxs:
    print(f"  L{idx+1}: {lines[idx].rstrip()}")

# Comment out the bad rule header + its indented recipe lines
out = []
skip = set(bad_idxs)
i = 0
while i < len(lines):
    if i in skip:
        out.append(f"# FG_DOCTOR_DISABLED: {lines[i]}")
        i += 1
        # also comment out its recipe lines (tab-indented)
        while i < len(lines) and lines[i].startswith("\t"):
            out.append(f"# FG_DOCTOR_DISABLED: {lines[i]}")
            i += 1
        continue
    out.append(lines[i])
    i += 1

p.write_text("".join(out))
print("✅ Commented out the broken multi-pattern rules so make can parse again.")
PY
