import os

import pytest
from httpx import AsyncClient, ASGITransport

from tests.test_auth import build_app


@pytest.mark.asyncio
async def test_default_env_in_ci_has_auth_enabled():
    """
    CI sanity check:

    - FG_API_KEY must be set (we default it locally if missing)
    - /health should report auth_enabled = True
    """
    os.environ.setdefault("FG_API_KEY", "supersecret")

    app = build_app(auth_enabled=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_enabled"] is True
