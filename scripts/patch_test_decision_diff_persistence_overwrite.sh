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


def _post_auth(client: TestClient, attempts: int):
    r = client.post(
        "/defend",
        headers={"x-api-key": API_KEY},
        json={
            "event_type": "auth_attempt",
            "source": "pytest",
            "payload": {"source_ip": "1.2.3.4", "attempts": attempts},
        },
    )
    assert r.status_code == 200, f"/defend failed {r.status_code}: {r.text}"
    return r.json()


def test_decision_diff_is_persisted_and_surfaced(tmp_path):
    # Protect suite from env leakage
    keys = ["FG_API_KEY", "FG_AUTH_ENABLED", "FG_SQLITE_PATH"]
    old = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["FG_API_KEY"] = API_KEY
        os.environ["FG_AUTH_ENABLED"] = "1"
        os.environ["FG_SQLITE_PATH"] = str(tmp_path / "frostgate-test.db")

        app = build_app(auth_enabled=True)

        with TestClient(app) as client:
            # Two posts to create prior state + changed state
            _post_auth(client, 1)
            _post_auth(client, 10)

            r = client.get("/decisions", params={"limit": 1}, headers={"x-api-key": API_KEY})
            assert r.status_code == 200, f"/decisions failed {r.status_code}: {r.text}"
            item = r.json()["items"][0]

            diff = item.get("decision_diff")
            assert diff is not None, f"missing decision_diff: item={item}"
            assert diff.get("summary"), f"missing diff.summary: diff={diff}"
            changes = diff.get("changes") or []
            assert len(changes) >= 1, f"diff.changes empty: diff={diff}"

            # Make sure it’s meaningful: threat/decision/score change
            fields = set(changes)
            assert ({"threat_level", "decision", "score"} & fields), f"diff not meaningful: fields={fields}, diff={diff}"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
PY

python -m py_compile "$FILE"
echo "✅ Patched + compiled: $FILE"
