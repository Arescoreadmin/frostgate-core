#!/usr/bin/env bash
set -euo pipefail

f="Makefile"
test -f "$f" || { echo "❌ Makefile not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("Makefile")
lines = p.read_text().splitlines(True)

# find all "e2e-local:" target lines
idxs = [i for i,l in enumerate(lines) if re.match(r"^e2e-local\s*:", l)]
if len(idxs) <= 1:
    print("✅ No duplicate e2e-local targets found.")
    raise SystemExit(0)

keep = idxs[0]
drop = idxs[1]

def block_end(start):
    # End when we hit a non-indented line that looks like a new target (foo:)
    i = start + 1
    while i < len(lines):
        if re.match(r"^[A-Za-z0-9_.-]+\s*:", lines[i]) and not lines[i].startswith("\t"):
            break
        i += 1
    return i

end = block_end(drop)
del lines[drop:end]

p.write_text("".join(lines))
print(f"✅ Removed duplicate e2e-local block at lines ~{drop+1}-{end}")
PY
