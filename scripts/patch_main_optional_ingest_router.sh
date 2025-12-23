#!/usr/bin/env bash
set -euo pipefail

FILE="api/main.py"
if [[ ! -f "$FILE" ]]; then
  echo "ERROR: $FILE not found"
  exit 1
fi

echo "[*] Making ingest router optional in $FILE ..."

python - <<'PY'
from pathlib import Path
import re

p = Path("api/main.py")
s = p.read_text()

# Replace the hard import:
# from api.ingest import router as ingest_router
pat = r'^\s*from\s+api\.ingest\s+import\s+router\s+as\s+ingest_router\s*#\s*noqa:\s*E402\s*$'
if re.search(pat, s, flags=re.M):
    s = re.sub(
        pat,
        "try:\n    from api.ingest import router as ingest_router  # noqa: E402\nexcept Exception as e:  # pragma: no cover\n    ingest_router = None\n    import logging\n    logging.getLogger(__name__).exception(\"ingest router disabled: %s\", e)",
        s,
        flags=re.M
    )
else:
    # If it's without the comment
    pat2 = r'^\s*from\s+api\.ingest\s+import\s+router\s+as\s+ingest_router\s*$'
    if re.search(pat2, s, flags=re.M):
        s = re.sub(
            pat2,
            "try:\n    from api.ingest import router as ingest_router\nexcept Exception as e:  # pragma: no cover\n    ingest_router = None\n    import logging\n    logging.getLogger(__name__).exception(\"ingest router disabled: %s\", e)",
            s,
            flags=re.M
        )

# Now ensure include_router is conditional
# app.include_router(ingest_router)
s = re.sub(
    r'^\s*app\.include_router\(\s*ingest_router\s*\)\s*$',
    "if ingest_router is not None:\n    app.include_router(ingest_router)",
    s,
    flags=re.M
)

p.write_text(s)
print("[+] Patched api/main.py")
PY

echo "[*] Done."
