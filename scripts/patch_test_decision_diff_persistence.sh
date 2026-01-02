#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-backend/tests/test_decision_diff_persistence.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing file: $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

cat > "$FILE" <<'PY'
import os

import pytest
from fastapi.testclient import TestClient

from api.main import build_app

API_KEY = os.getenv("FG_API_KEY", "supersecret")


@pytest.fixture(scope="function")
def client(tmp_path):
    """
    Self-contained app client:
    - No dependency on an already-running uvicorn
    - Deterministic auth key
    - SQLite file isolated per test
    """
    os.environ["FG_API_KEY"] = API_KEY
    os.environ["FG_AUTH_ENABLED"] = "1"

    # Keep DB isolated so the test doesn't depend on whatever junk is in /state
    db_path = tmp_path / "frostgate-test.db"
    os.environ["FG_SQLITE_PATH"] = str(db_path)

    app = build_app(auth_enabled=True)

    with TestClient(app) as c:
        yield c


def _post_auth(c: TestClient, attempts: int) -> dict:
    r = c.post(
        "/defend",
        headers={"x-api-key": API_KEY},
        json={
            "event_type": "auth_attempt",
            "source": "pytest",
            "payload": {"source_ip": "1.2.3.4", "attempts": int(attempts)},
        },
    )
    assert r.status_code == 200, f"/defend failed: {r.status_code} {r.text}"
    return r.json()


def test_decision_diff_is_persisted_and_surfaced(client: TestClient):
    _post_auth(client, 1)
    _post_auth(client, 10)

    r = client.get("/decisions?limit=1", headers={"x-api-key": API_KEY})
    assert r.status_code == 200, f"/decisions failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("items"), f"decisions returned no items: {data}"

    item = data["items"][0]
    diff = item.get("decision_diff")
    assert diff is not None, f"missing decision_diff in item: {item}"
    assert diff.get("summary"), f"diff summary missing/empty: {diff}"
    assert diff.get("changes"), f"diff changes missing/empty: {diff}"

    changes = diff["changes"]
    fields = set()

    # Support both schemas:
    # - list[str]
    # - list[dict] with {field/name}
    if all(isinstance(c, str) for c in changes):
        fields = set(changes)
    else:
        for c in changes:
            if isinstance(c, dict):
                f = c.get("field") or c.get("name")
                if f:
                    fields.add(f)

    assert fields, f"could not parse changed fields from diff: {diff}"
    assert (
        ("threat_level" in fields)
        or ("score" in fields)
        or ("decision" in fields)
    ), f"diff not meaningful enough: fields={fields}, diff={diff}"
PY

python -m py_compile "$FILE"
echo "✅ Patched + compiled: $FILE"
