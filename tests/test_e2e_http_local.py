import os
import time
import pytest
import requests

pytestmark = pytest.mark.e2e_http

BASE_URL = os.getenv("FG_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("FG_API_KEY", "supersecret")

def _h():
    return {"X-API-Key": API_KEY}

def _wait_health(timeout_s: float = 10.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"/health not ready at {BASE_URL}")

def test_http_health_and_auth():
    _wait_health()
    r = requests.get(f"{BASE_URL}/feed/live?limit=1", timeout=5)
    assert r.status_code == 401

    r = requests.get(f"{BASE_URL}/feed/live?limit=1", headers={"X-API-Key": "wrong"}, timeout=5)
    assert r.status_code == 401

    r = requests.get(f"{BASE_URL}/feed/live?limit=1", headers=_h(), timeout=5)
    assert r.status_code == 200

def test_http_seed_and_actionable_filter():
    _wait_health()

    # seed must exist in this e2e tier (dev enabled)
    r = requests.post(f"{BASE_URL}/dev/seed", headers=_h(), timeout=10)
    assert r.status_code in (200, 201)

    r = requests.get(f"{BASE_URL}/feed/live?limit=200&only_actionable=true", headers=_h(), timeout=10)
    assert r.status_code == 200
    items = r.json()["items"]

    for it in items:
        if it.get("source") == "dev_seed":
            if it.get("action_taken") == "log_only" and it.get("severity") in ("low", "info"):
                raise AssertionError("only_actionable leaked dev_seed noise over real HTTP")
