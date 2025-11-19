from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.main import app

import os

API_KEY = os.getenv("FG_API_KEY", "supersecret")


client = TestClient(app)


def _base_payload(failed_auths: int) -> dict:
    return {
        "source": "edge-gateway-1",
        "tenant_id": "tenant-test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "event_type": "auth",
            "src_ip": "192.0.2.10",
            "failed_auths": failed_auths,
        },
    }


def test_defend_high_bruteforce_response():
    payload = _base_payload(failed_auths=12)

    resp = client.post(
    "/defend",
    headers={
        "Content-Type": "application/json",
        "x-pq-fallback": "1",
        "x-api-key": "supersecret",  # or read from env if you prefer
    },
    json=payload,
)
    assert resp.status_code == 200

    data = resp.json()
    assert data["threat_level"] == "high"
    assert any(
        m["action"] in {"block_ip", "log_only"} and m["target"] == "192.0.2.10"
        for m in data["mitigations"]
    )
    assert "rules_triggered" in data["explain"]
    assert "ssh_bruteforce" in " ".join(data["explain"]["rules_triggered"])


def test_health_endpoint_alive():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"
    assert "env" in body
