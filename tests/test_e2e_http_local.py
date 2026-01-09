"""
HTTP E2E tests (local/dev).

Behavior:
- SKIPPED unless FG_E2E_HTTP=1 (or true/yes).
- Assumes FrostGate Core API is reachable at FG_BASE_URL.
- Validates only stable contract-ish behavior (low drift).

Env:
  FG_E2E_HTTP=1
  FG_BASE_URL=http://127.0.0.1:8000
  FG_API_KEY=supersecret
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import pytest
import requests


BASE_URL = os.getenv("FG_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("FG_API_KEY", "supersecret")

E2E_ENABLED = os.getenv("FG_E2E_HTTP", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "y",
    "on",
)

pytestmark = pytest.mark.e2e_http


def _headers(with_key: bool = True) -> Dict[str, str]:
    if not with_key:
        return {}
    return {"X-API-Key": API_KEY}


def _req(
    method: str,
    path: str,
    *,
    with_key: bool = True,
    timeout: float = 3.0,
    json: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    url = f"{BASE_URL}{path}"
    return requests.request(
        method, url, headers=_headers(with_key=with_key), json=json, timeout=timeout
    )


def _wait_for_health(timeout_s: float = 20.0) -> None:
    """
    Wait until /health returns 200. If it never comes up, fail with a helpful message.
    """
    t0 = time.time()
    last: Optional[str] = None
    while time.time() - t0 < timeout_s:
        try:
            r = _req("GET", "/health", with_key=False, timeout=1.0)
            if r.status_code == 200:
                return
            last = f"status={r.status_code}, body={r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(0.2)

    raise AssertionError(
        f"API not reachable at {BASE_URL} after {timeout_s}s.\n"
        f"Last error: {last}\n\n"
        f"Fix:\n"
        f"  1) Start server:  bash scripts/uvicorn_local.sh start\n"
        f"  2) Run tests:     FG_E2E_HTTP=1 FG_BASE_URL={BASE_URL} FG_API_KEY=... pytest -q -m e2e_http\n"
        f"Or use: make fg-up && make fg-e2e-http\n"
    )


def _auth_required_for(path: str) -> bool:
    """
    Probe an endpoint without API key; if 401/403, assume auth is required.
    """
    try:
        r = _req("GET", path, with_key=False, timeout=2.0)
        return r.status_code in (401, 403)
    except Exception:
        # conservative
        return True


def _json_or_fail(r: requests.Response) -> Any:
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise AssertionError(
            f"Expected JSON, got status={r.status_code}, body={r.text[:400]} err={e!r}"
        )


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="FG_E2E_HTTP not enabled (set FG_E2E_HTTP=1 to run HTTP e2e tests)",
)
def test_http_health_ready() -> None:
    _wait_for_health()

    r = _req("GET", "/health", with_key=False)
    assert r.status_code == 200

    r2 = _req("GET", "/health/ready", with_key=False)
    if r2.status_code != 404:
        assert r2.status_code == 200


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="FG_E2E_HTTP not enabled (set FG_E2E_HTTP=1 to run HTTP e2e tests)",
)
def test_http_openapi_exists_and_has_core_paths() -> None:
    _wait_for_health()

    r = _req("GET", "/openapi.json", with_key=False)
    assert r.status_code == 200

    spec = _json_or_fail(r)
    assert isinstance(spec, dict)
    assert "paths" in spec and isinstance(spec["paths"], dict)

    paths = spec["paths"]
    # Always-present core paths
    assert "/health" in paths
    assert "/health/ready" in paths
    assert "/feed/live" in paths

    # MVP “feels real” endpoints that should exist
    assert "/defend" in paths or "/v1/defend" in paths
    assert "/decisions" in paths
    assert "/stats" in paths


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="FG_E2E_HTTP not enabled (set FG_E2E_HTTP=1 to run HTTP e2e tests)",
)
def test_http_feed_live_shape_and_auth() -> None:
    _wait_for_health()

    feed_path = "/feed/live"
    needs_auth = _auth_required_for(feed_path)

    r_no = _req("GET", feed_path, with_key=False)
    if needs_auth:
        assert r_no.status_code in (401, 403)
    else:
        assert r_no.status_code in (200, 204)

    r = _req("GET", feed_path, with_key=True)
    assert r.status_code in (200, 204), (
        f"{feed_path} unexpected: {r.status_code} {r.text[:200]}"
    )

    if r.status_code == 204:
        return

    data = _json_or_fail(r)

    # tolerate common patterns: list or {"items":[...]} etc
    if isinstance(data, dict):
        for k in ("items", "events", "data"):
            if k in data and isinstance(data[k], list):
                data = data[k]
                break

    assert isinstance(data, list), f"Expected list-ish feed payload; got {type(data)}"

    if data:
        item = data[0]
        assert isinstance(item, dict)
        # minimal “this is an event-ish thing” sanity keys
        key_candidates = (
            "id",
            "event_id",
            "ts",
            "timestamp",
            "type",
            "severity",
            "risk",
            "score",
            "hash",
        )
        assert any(k in item for k in key_candidates), (
            f"Feed item missing expected keys: {item.keys()}"
        )


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="FG_E2E_HTTP not enabled (set FG_E2E_HTTP=1 to run HTTP e2e tests)",
)
def test_http_dev_seed_and_emit_optional() -> None:
    """
    /dev/seed and /dev/emit exist in your repo. They should work with API key.
    If you later disable them in prod, keep them in dev builds.
    """
    _wait_for_health()

    # seed
    r_seed = _req("POST", "/dev/seed", with_key=True, json={})
    assert r_seed.status_code in (200, 204), (
        f"/dev/seed unexpected: {r_seed.status_code} {r_seed.text[:200]}"
    )

    # emit (lightweight, doesn't assume schema beyond being accepted)
    r_emit = _req("POST", "/dev/emit", with_key=True, json={"count": 1})
    assert r_emit.status_code in (200, 204), (
        f"/dev/emit unexpected: {r_emit.status_code} {r_emit.text[:200]}"
    )


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="FG_E2E_HTTP not enabled (set FG_E2E_HTTP=1 to run HTTP e2e tests)",
)
def test_http_stats_debug_optional() -> None:
    """
    main.py exposes /stats/debug. Validate it's reachable (best-effort).
    """
    _wait_for_health()

    r = _req("GET", "/stats/debug", with_key=False)
    if r.status_code == 404:
        pytest.skip("/stats/debug not exposed in this build")

    assert r.status_code == 200, (
        f"/stats/debug unexpected: {r.status_code} {r.text[:200]}"
    )
    data = _json_or_fail(r)
    assert isinstance(data, dict)
