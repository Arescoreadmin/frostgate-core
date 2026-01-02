#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-backend/tests/test_decision_diff_persistence.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("backend/tests/test_decision_diff_persistence.py")
s = p.read_text(encoding="utf-8")

# Replace the client fixture with an env-safe version.
# We’ll do a targeted replace from "def client(" to "yield c" block.
pat = r"@pytest\.fixture\(scope=\"function\"\)\ndef client\(tmp_path\):\n(?:[ \t].*\n)+?\s+yield c\n"
m = re.search(pat, s, flags=re.M)
if not m:
    raise SystemExit("PATCH FAILED: couldn't find client(tmp_path) fixture block")

replacement = """@pytest.fixture(scope="function")
def client(tmp_path):
    \"""
    Self-contained app client:
    - No dependency on an already-running uvicorn
    - Deterministic auth key
    - SQLite file isolated per test
    - Restores env afterwards (so other tests don't explode)
    \"""
    # Snapshot current env so we don't poison the rest of the test suite
    keys = ["FG_API_KEY", "FG_AUTH_ENABLED", "FG_SQLITE_PATH"]
    old = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["FG_API_KEY"] = API_KEY
        os.environ["FG_AUTH_ENABLED"] = "1"

        db_path = tmp_path / "frostgate-test.db"
        os.environ["FG_SQLITE_PATH"] = str(db_path)

        app = build_app(auth_enabled=True)
        with TestClient(app) as c:
            yield c
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
"""

s2 = re.sub(pat, replacement, s, flags=re.M)
p.write_text(s2, encoding="utf-8")
print("✅ Patched decision diff test fixture to restore env")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK: $FILE"
