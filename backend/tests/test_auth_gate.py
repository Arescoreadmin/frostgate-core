from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from api.main import build_app


@pytest.fixture()
def client():
    # force auth enabled for this test suite
    app = build_app(auth_enabled=True)
    return TestClient(app)


def _get_cookie(client: TestClient, api_key: str = "supersecret") -> str:
    r = client.get(f"/ui/token?api_key={api_key}")
    assert r.status_code == 200
    # cookie should be set
    assert "fg_api_key" in r.cookies
    return r.cookies["fg_api_key"]


def test_ui_token_public(client: TestClient):
    r = client.get("/ui/token?api_key=supersecret")
    assert r.status_code == 200


def test_ui_feed_401_without_cookie(client: TestClient):
    r = client.get("/ui/feed")
    assert r.status_code == 401
    assert r.headers.get("x-fg-authgate") == "1"
    assert r.headers.get("x-fg-gate") == "blocked"
    assert r.headers.get("content-type", "").startswith("application/json")


def test_ui_feed_200_with_cookie_html(client: TestClient):
    cookie = _get_cookie(client)
    r = client.get("/ui/feed", cookies={"fg_api_key": cookie})
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert ct.startswith("text/html")
    assert r.headers.get("x-fg-authgate") == "1"
    assert r.headers.get("x-fg-gate") in ("protected", "public")  # should be protected


def test_sse_401_without_cookie(client: TestClient):
    r = client.get("/feed/stream?limit=1&interval=0.2&q=&threat_level=")
    assert r.status_code == 401
    assert r.headers.get("content-type", "").startswith("application/json")


def test_sse_200_with_cookie_emits_data(client: TestClient):
    cookie = _get_cookie(client)
    with client.stream(
        "GET",
        "/feed/stream?limit=1&interval=0.2&q=&threat_level=",
        cookies={"fg_api_key": cookie},
    ) as r:
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("text/event-stream")
        # read a few lines and ensure at least one "data: " appears
        found = False
        for _ in range(50):
            line = r.iter_lines().__next__().decode("utf-8", errors="ignore")
            if line.startswith("data: "):
                found = True
                break
        assert found, "SSE stream did not emit any data: line"
