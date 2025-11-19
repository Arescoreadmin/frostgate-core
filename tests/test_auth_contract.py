# tests/test_auth_contract.py
import os
from httpx import AsyncClient
from api.main import app  # uses real import path + config

import pytest


@pytest.mark.asyncio
async def test_default_env_in_ci_has_auth_enabled():
    # This is basically a sanity check to ensure CI env stays consistent
    assert os.getenv("FG_API_KEY"), "FG_API_KEY must be set in CI"

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is True
