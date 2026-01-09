from fastapi.testclient import TestClient


def test_roe_not_mounted_when_disabled(build_app, monkeypatch):
    monkeypatch.delenv("FG_GOVERNANCE_ENABLED", raising=False)
    monkeypatch.delenv("FG_MISSION_ENVELOPE_ENABLED", raising=False)
    monkeypatch.delenv("FG_RING_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("FG_FORENSICS_ENABLED", raising=False)
    monkeypatch.delenv("FG_ROE_ENGINE_ENABLED", raising=False)
    app = build_app()
    client = TestClient(app)

    r = client.post("/roe/evaluate", headers={"X-API-Key": "supersecret"}, json={})
    assert r.status_code == 404


def test_roe_gating_contract(build_app, monkeypatch):
    monkeypatch.setenv("FG_ROE_ENGINE_ENABLED", "1")
    app = build_app()

    client = TestClient(app)
    headers = {"X-API-Key": "supersecret"}

    resp = client.post(
        "/roe/evaluate",
        headers=headers,
        json={
            "persona": "guardian",
            "classification": "SECRET",
            "mitigations": [{"action": "block_ip"}],
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["gating_decision"] == "require_approval"
