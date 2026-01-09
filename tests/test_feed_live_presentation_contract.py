import os
import importlib
from fastapi.testclient import TestClient

API_KEY = "supersecret"


def build_app(auth_enabled: bool):
    # Always set auth env before importing main (since main/build_app may read env)
    os.environ["FG_AUTH_ENABLED"] = "1" if auth_enabled else "0"
    os.environ["FG_API_KEY"] = API_KEY

    import api.main as main

    importlib.reload(main)
    return main.build_app(auth_enabled)


def test_feed_live_items_have_presentation_fields(tmp_path):
    # Set DB path BEFORE anything imports/initializes DB
    os.environ["FG_SQLITE_PATH"] = str(tmp_path / "fg.db")
    os.environ["FG_DEV_EVENTS_ENABLED"] = "1"

    # Ensure schema exists in the sqlite file we just pointed at
    import api.db as db

    importlib.reload(db)  # make sure it picks up env-driven path
    db.init_db()

    app = build_app(True)
    c = TestClient(app)

    # emit some events
    r = c.post(
        "/dev/emit?count=5&kind=waf&threat_level=high",
        headers={"x-api-key": API_KEY},
    )
    assert r.status_code in (200, 204), r.text

    resp = c.get("/feed/live?limit=5", headers={"x-api-key": API_KEY})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["items"], "expected feed items"

    i = data["items"][0]
    # hard contract
    for k in [
        "timestamp",
        "severity",
        "title",
        "summary",
        "action_taken",
        "confidence",
        "score",
    ]:
        assert k in i, f"missing {k}"
        assert i[k] is not None, f"{k} is None"
