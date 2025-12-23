#!/usr/bin/env bash
set -euo pipefail
FILE="api/schemas.py"
[[ -f "$FILE" ]] || { echo "ERROR: $FILE not found"; exit 1; }

python - <<'PY'
from pathlib import Path

p = Path("api/schemas.py")
s = p.read_text()

if "class MitigationAction" in s:
    print("[=] MitigationAction already exists")
    raise SystemExit(0)

# Insert near top, after imports. If we can't find a clean spot, append.
insert = """
# --- compatibility shim: MitigationAction (used by engine/rules.py) ---
from typing import Optional, Literal
from pydantic import BaseModel

class MitigationAction(BaseModel):
    action: Literal["allow", "block", "rate_limit", "challenge", "quarantine"] = "allow"
    reason: Optional[str] = None
    ttl_seconds: Optional[int] = None
# --- end shim ---
"""

lines = s.splitlines(True)
out = []
inserted = False
for i, line in enumerate(lines):
    out.append(line)
    # after last import block
    if not inserted and line.startswith("from") or line.startswith("import"):
        # keep collecting imports
        pass

# Simple heuristic: insert after the first blank line following initial imports
if not inserted:
    out2 = []
    in_header = True
    for line in lines:
        out2.append(line)
        if in_header and line.strip() == "":
            out2.append(insert)
            inserted = True
            in_header = False
    if not inserted:
        out2.append("\n"+insert)
    p.write_text("".join(out2))
    print("[+] Added MitigationAction shim to api/schemas.py")
PY
