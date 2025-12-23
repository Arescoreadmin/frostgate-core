#!/usr/bin/env bash
set -euo pipefail
FILE="api/schemas.py"
[[ -f "$FILE" ]] || { echo "ERROR: $FILE not found"; exit 1; }

python - <<'PY'
from pathlib import Path

p = Path("api/schemas.py")
s = p.read_text()

if "class ClassificationRing" in s:
    print("[=] ClassificationRing already exists")
    raise SystemExit(0)

insert = """
# --- compatibility shim: ClassificationRing ---
from enum import Enum

class ClassificationRing(str, Enum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    secret = "secret"
# --- end shim ---
"""

# append near the other shim or end of file
p.write_text(s + ("\n" if not s.endswith("\n") else "") + insert)
print("[+] Added ClassificationRing to api/schemas.py")
PY
