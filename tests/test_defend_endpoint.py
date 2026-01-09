from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.auth_scopes import mint_key


@pytest.fixture()
def client():
    return TestClient(app)


def _payload(failed_auths=12):
    return {
        "event_type": "auth.bruteforce",
        "tenant_id": "test-tenant",
        "source": "unit-test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {"src_ip": "1.2.3.4", "failed_auths": failed_auths},
    }


def test_defend_high_bruteforce_response(client):
    key = mint_key("defend:write")

    resp = client.post(
        "/defend",
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "x-pq-fallback": "1",
        },
        json=_payload(12),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("threat_level") in ("high", "critical"), body


def test_health_endpoint_alive(client):
    r = client.get("/health")
    if r.status_code == 404:
        r = client.get("/health/live")
    assert r.status_code in (200, 204), r.text
