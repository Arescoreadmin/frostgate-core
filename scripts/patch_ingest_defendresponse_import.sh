#!/usr/bin/env bash
set -euo pipefail

FILE="api/ingest.py"

if [[ ! -f "$FILE" ]]; then
  echo "ERROR: $FILE not found"
  exit 1
fi

echo "[*] Patching $FILE imports (DefendResponse -> api.models, TelemetryInput -> api.schemas)"

# If the bad import line exists, replace it with correct imports.
python - <<'PY'
import re, pathlib, sys

p = pathlib.Path("api/ingest.py")
s = p.read_text()

bad = r"from\s+api\.schemas\s+import\s+DefendResponse\s*,\s*TelemetryInput\s*$"
good = "from api.models import DefendResponse\nfrom api.schemas import TelemetryInput"

if re.search(bad, s, flags=re.M):
    s2 = re.sub(bad, good, s, flags=re.M)
    p.write_text(s2)
    print("[+] Replaced: from api.schemas import DefendResponse, TelemetryInput")
else:
    # If it's already split or different, ensure we still end up correct.
    # Remove any DefendResponse import from api.schemas
    s2 = re.sub(r"^from\s+api\.schemas\s+import\s+([^\n]*\bDefendResponse\b[^\n]*)\n", "", s, flags=re.M)

    # Ensure TelemetryInput import exists from api.schemas
    if not re.search(r"^from\s+api\.schemas\s+import\s+.*\bTelemetryInput\b", s2, flags=re.M):
        s2 = re.sub(r"^(from\s+fastapi\b[^\n]*\n)", r"\1from api.schemas import TelemetryInput\n", s2, flags=re.M) if "from fastapi" in s2 else ("from api.schemas import TelemetryInput\n" + s2)

    # Ensure DefendResponse import exists from api.models
    if not re.search(r"^from\s+api\.models\s+import\s+.*\bDefendResponse\b", s2, flags=re.M):
        # Insert near top after other api.* imports
        lines = s2.splitlines(True)
        insert_at = 0
        for i, line in enumerate(lines[:80]):
            if line.startswith("from api.") or line.startswith("import api."):
                insert_at = i + 1
        lines.insert(insert_at, "from api.models import DefendResponse\n")
        s2 = "".join(lines)

    p.write_text(s2)
    print("[+] Normalized imports (removed DefendResponse from api.schemas, added api.models DefendResponse)")
PY

echo "[*] Showing resulting import block:"
python - <<'PY'
import itertools
from pathlib import Path
p=Path("api/ingest.py")
for i,line in enumerate(p.read_text().splitlines(), start=1):
    if i>40: break
    if "from api." in line or "import api." in line:
        print(f"{i:>4}: {line}")
PY

echo "[*] Done. Rebuild + restart frostgate-core next."
