#!/usr/bin/env bash
set -euo pipefail

FILE="api/main.py"
[[ -f "$FILE" ]] || { echo "ERROR: $FILE not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text()

# Any pattern like: from api.X import router as X_router
# becomes a try/except import that sets X_router=None
def repl(m):
    mod = m.group(1)
    var = m.group(2)
    return (
        f"try:\n"
        f"    from api.{mod} import router as {var}\n"
        f"except Exception as e:  # pragma: no cover\n"
        f"    {var} = None\n"
        f"    import logging\n"
        f"    logging.getLogger(__name__).exception(\"router disabled: api.{mod} (%s)\", e)\n"
    )

s2 = re.sub(
    r'^\s*from\s+api\.(\w+)\s+import\s+router\s+as\s+(\w+)\s*(?:#.*)?$',
    repl,
    s,
    flags=re.M
)

# Any include_router(var) becomes conditional
s2 = re.sub(
    r'^\s*app\.include_router\(\s*(\w+)\s*\)\s*$',
    r'if \1 is not None:\n    app.include_router(\1)',
    s2,
    flags=re.M
)

if s2 != s:
    p.write_text(s2)
    print("[+] Patched api/main.py (router imports non-fatal)")
else:
    print("[=] No changes made")
PY

echo "[*] Done."
