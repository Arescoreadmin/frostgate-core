import os
import importlib

import pytest
from httpx import AsyncClient, ASGITransport


def build_app(auth_enabled: bool):
    """
    Rebuild the FastAPI app with a clean config and desired auth_enabled state.

    Assumes:
      - FG_API_KEY env var is used in api.config.Settings
      - api.config exposes get_settings() cached with lru_cache (we clear it if present)
      - api.main imports config and defines `app`
    """
    # 1) Set / clear the key in the environment
    if auth_enabled:
        os.environ["FG_API_KEY"] = "supersecret"
    else:
        os.environ.pop("FG_API_KEY", None)

    # 2) Reload config so settings re-read env
    import api.config as config
    get_settings = getattr(config, "get_settings", None)
    if get_settings is not None and hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    importlib.reload(config)

    # 3) Reload main so it picks up updated settings & rewires routes
    import api.main as main
    importlib.reload(main)

    return main.app


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_enabled", [False, True])
async def test_health_reflects_auth_enabled(auth_enabled: bool):
    """
    /health should always be 200 and report auth_enabled
    according to FG_API_KEY presence.
    """
    app = build_app(auth_enabled)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200

        data = resp.json()
        assert "auth_enabled" in data
        assert data["auth_enabled"] is auth_enabled


@pytest.mark.asyncio
async def test_status_requires_key_when_auth_enabled():
    """
    With FG_API_KEY set:
      - /status should 401 without key
    """
    app = build_app(auth_enabled=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/status")
        assert resp.status_code == 401
        body = resp.json()
        assert body.get("detail") == "Invalid or missing API key"


@pytest.mark.asyncio
async def test_v1_status_accepts_valid_key_and_rejects_missing():
    """
    With FG_API_KEY set:
      - /v1/status should 200 with correct x-api-key
      - /v1/status should 401 without key
    """
    app = build_app(auth_enabled=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No key -> 401
        resp_no_key = await client.get("/v1/status")
        assert resp_no_key.status_code == 401
        assert resp_no_key.json().get("detail") == "Invalid or missing API key"

        # Valid key -> 200
        resp = await client.get("/v1/status", headers={"x-api-key": "supersecret"})
        assert resp.status_code == 200

        data = resp.json()
        assert data.get("service") == "frostgate-core"
        assert data.get("env") == "dev"
