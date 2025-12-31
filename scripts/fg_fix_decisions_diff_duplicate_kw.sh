#!/usr/bin/env bash
set -euo pipefail

f="api/decisions.py"
if [[ ! -f "$f" ]]; then
  echo "❌ $f not found"
  exit 1
fi

echo "==> Removing duplicate decision_diff= kwargs inside DecisionOut(...) blocks in $f"

python - <<'PY'
from pathlib import Path

p = Path("api/decisions.py")
lines = p.read_text().splitlines(True)

out = []
in_block = False
paren = 0
seen_decision_diff = False

def line_opens_decisionout(l: str) -> bool:
    return "DecisionOut(" in l

for l in lines:
    if not in_block and line_opens_decisionout(l):
        in_block = True
        paren = l.count("(") - l.count(")")
        seen_decision_diff = False
        out.append(l)
        continue

    if in_block:
        # If we already saw decision_diff= in this DecisionOut(...) call, drop duplicates
        if "decision_diff=" in l:
            if seen_decision_diff:
                # Skip this duplicate kwarg line
                continue
            seen_decision_diff = True

        out.append(l)
        paren += l.count("(") - l.count(")")
        if paren <= 0:
            in_block = False
        continue

    out.append(l)

p.write_text("".join(out))
print("✅ Deduped decision_diff= kwargs inside DecisionOut(...) blocks")
PY

echo "==> Quick syntax check"
.venv/bin/python -m py_compile api/decisions.py

echo "==> Run tests"
./scripts/test.sh
