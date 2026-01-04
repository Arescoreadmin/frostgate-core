import os
from fastapi.testclient import TestClient
from api.main import build_app

API_KEY = "supersecret"

def test_feed_live_presentation_fields_present(tmp_path):
    old = {k: os.environ.get(k) for k in ["FG_API_KEY", "FG_AUTH_ENABLED", "FG_SQLITE_PATH", "FG_DEV_EVENTS_ENABLED"]}
    try:
        os.environ["FG_API_KEY"] = API_KEY
        os.environ["FG_AUTH_ENABLED"] = "1"
        os.environ["FG_SQLITE_PATH"] = str(tmp_path / "frostgate-test.db")
        os.environ["FG_DEV_EVENTS_ENABLED"] = "1"

        app = build_app(auth_enabled=True)
        with TestClient(app) as client:
            r = client.post("/dev/seed", headers={"x-api-key": API_KEY})
            assert r.status_code == 200, r.text

            r = client.get("/feed/live?limit=1", headers={"x-api-key": API_KEY})
            assert r.status_code == 200, r.text
            item = r.json()["items"][0]

            assert item.get("severity") in ("info","low","medium","high","critical")
            assert item.get("action_taken") in ("log_only","blocked","rate_limited","quarantined")
            assert isinstance(item.get("score"), (int, float))
            assert 0 <= float(item["score"]) <= 100
            assert isinstance(item.get("confidence"), (int, float))
            assert 0 <= float(item["confidence"]) <= 1
            assert item.get("title")
            assert item.get("summary")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
