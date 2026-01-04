import os
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

# Uses your unified harness (already in place)
# build_app fixture exists from tests/conftest.py via build_app_factory

REQUIRED_PRESENTATION_FIELDS = (
    "timestamp",
    "severity",
    "title",
    "summary",
    "action_taken",
    "confidence",
    "score",
)

def _auth_headers() -> Dict[str, str]:
    return {"X-API-Key": os.getenv("FG_API_KEY", "supersecret")}


def test_auth_required_when_enabled(build_app):
    app = build_app(auth_enabled=True)
    c = TestClient(app)

    r = c.get("/feed/live?limit=1")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or missing API key"

    r = c.get("/feed/live?limit=1", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

    r = c.get("/feed/live?limit=1", headers=_auth_headers())
    assert r.status_code == 200


def test_feed_presentation_fields_non_null(build_app):
    app = build_app(auth_enabled=True, dev_events_enabled=True)
    c = TestClient(app)

    # seed deterministically
    r = c.post("/dev/seed", headers=_auth_headers())
    assert r.status_code in (200, 201)

    r = c.get("/feed/live?limit=50", headers=_auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("items"), list)
    assert len(data["items"]) >= 1

    for item in data["items"]:
        for k in REQUIRED_PRESENTATION_FIELDS:
            assert k in item, f"missing {k}"
            assert item[k] is not None, f"{k} is null"


def test_only_actionable_filters_dev_seed_noise(build_app):
    app = build_app(auth_enabled=True, dev_events_enabled=True)
    c = TestClient(app)

    r = c.post("/dev/seed", headers=_auth_headers())
    assert r.status_code in (200, 201)

    r = c.get("/feed/live?limit=200&only_actionable=true", headers=_auth_headers())
    assert r.status_code == 200
    items = r.json()["items"]

    # Contract: MUST NOT return dev_seed items that are low/info + log_only
    for it in items:
        if it.get("source") == "dev_seed":
            if it.get("action_taken") == "log_only" and it.get("severity") in ("low", "info"):
                pytest.fail("only_actionable filter leaked dev_seed noise item")


def test_dev_seed_gated_when_disabled(build_app):
    app = build_app(auth_enabled=True, dev_events_enabled=False)
    c = TestClient(app)

    r = c.post("/dev/seed", headers=_auth_headers())
    # preferred 404; 405 acceptable depending on router mount style
    assert r.status_code in (404, 405)
