import os
import importlib
import pytest
from fastapi.testclient import TestClient

API_KEY = "supersecret"

def build_app(auth_enabled: bool):
    os.environ["FG_AUTH_ENABLED"] = "1" if auth_enabled else "0"
    os.environ["FG_API_KEY"] = API_KEY
    import api.main as main
    importlib.reload(main)
    return main.build_app(auth_enabled)

def test_feed_live_items_have_presentation_fields(tmp_path):
    os.environ["FG_SQLITE_PATH"] = str(tmp_path / "fg.db")
    os.environ["FG_DEV_EVENTS_ENABLED"] = "1"

    app = build_app(True)
    c = TestClient(app)

    # emit some events
    r = c.post("/dev/emit?count=5&kind=waf&threat_level=high", headers={"x-api-key": API_KEY})
    assert r.status_code in (200, 204)

    data = c.get("/feed/live?limit=5", headers={"x-api-key": API_KEY}).json()
    assert data["items"]

    i = data["items"][0]
    # hard contract
    for k in ["timestamp", "severity", "title", "summary", "action_taken", "confidence", "score"]:
        assert k in i, f"missing {k}"
        assert i[k] is not None, f"{k} is None"
