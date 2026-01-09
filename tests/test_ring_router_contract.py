from fastapi.testclient import TestClient


def test_rings_not_mounted_when_disabled(build_app, monkeypatch):
    monkeypatch.delenv("FG_GOVERNANCE_ENABLED", raising=False)
    monkeypatch.delenv("FG_MISSION_ENVELOPE_ENABLED", raising=False)
    monkeypatch.delenv("FG_ROE_ENGINE_ENABLED", raising=False)
    monkeypatch.delenv("FG_FORENSICS_ENABLED", raising=False)
    monkeypatch.delenv("FG_RING_ROUTER_ENABLED", raising=False)
    app = build_app()
    client = TestClient(app)

    r = client.post(
        "/rings/route",
        headers={"X-API-Key": "supersecret"},
        json={"classification": "CUI"},
    )
    assert r.status_code == 404


def test_ring_router_contract(build_app, monkeypatch):
    monkeypatch.setenv("FG_RING_ROUTER_ENABLED", "1")
    app = build_app()

    client = TestClient(app)
    headers = {"X-API-Key": "supersecret"}

    resp = client.post("/rings/route", headers=headers, json={"classification": "CUI"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Implementation returns "state/cui/frostgate.db" and "models/cui/ensemble.pkl"
    db_path = str(data["db_path"]).replace("\\", "/")
    model_path = str(data["model_path"]).replace("\\", "/")

    assert db_path.endswith("cui/frostgate.db")
    assert model_path.endswith("cui/ensemble.pkl")

    iso = client.get("/rings/isolation?source=CUI&target=CUI", headers=headers)
    assert iso.status_code == 200, iso.text
    assert iso.json()["allowed"] is True
