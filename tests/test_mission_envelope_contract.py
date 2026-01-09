from fastapi.testclient import TestClient


def test_missions_not_mounted_when_disabled(build_app, monkeypatch):
    monkeypatch.delenv("FG_GOVERNANCE_ENABLED", raising=False)
    monkeypatch.delenv("FG_RING_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("FG_ROE_ENGINE_ENABLED", raising=False)
    monkeypatch.delenv("FG_FORENSICS_ENABLED", raising=False)
    monkeypatch.delenv("FG_MISSION_ENVELOPE_ENABLED", raising=False)
    app = build_app()
    client = TestClient(app)

    r = client.get("/missions", headers={"X-API-Key": "supersecret"})
    assert r.status_code == 404


def test_mission_envelope_routes(build_app, monkeypatch):
    monkeypatch.setenv("FG_MISSION_ENVELOPE_ENABLED", "1")
    app = build_app()

    client = TestClient(app)
    headers = {"X-API-Key": "supersecret"}

    resp = client.get("/missions", headers=headers)
    assert resp.status_code == 200, resp.text
    missions = resp.json()
    assert isinstance(missions, list)
    assert missions

    mission_id = missions[0]["mission_id"]
    detail = client.get(f"/missions/{mission_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["mission_id"] == mission_id

    status = client.get(f"/missions/{mission_id}/status", headers=headers)
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["mission_id"] == mission_id
    assert "active" in data
