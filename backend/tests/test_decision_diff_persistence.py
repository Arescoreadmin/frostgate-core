import os
import time
import requests

API_KEY = os.getenv("FG_API_KEY", "supersecret")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

def _post_auth(attempts: int):
    r = requests.post(
        f"{BASE_URL}/defend",
        headers={"content-type": "application/json", "x-api-key": API_KEY},
        json={
            "event_type": "auth",
            "source": "pytest",
            "payload": {"source_ip": "1.2.3.4", "attempts": attempts},
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def test_decision_diff_is_persisted_and_surfaced():
    _post_auth(1)
    _post_auth(10)

    r = requests.get(
        f"{BASE_URL}/decisions?limit=1",
        headers={"x-api-key": API_KEY},
        timeout=10,
    )
    r.raise_for_status()
    item = r.json()["items"][0]

    diff = item.get("decision_diff")
    assert diff is not None
    assert "summary" in diff and diff["summary"]
    assert "changes" in diff and len(diff["changes"]) >= 2

    # Prove it’s meaningful (threat change or score delta)
    fields = {c.get("field") for c in diff["changes"]}
    assert ("threat_level" in fields) or ("score" in fields)
