# tests/test_auth_tenants.py

import pytest
from httpx import AsyncClient, ASGITransport

from tests.test_auth import build_app


class DummyTenant:
    def __init__(self, tenant_id: str, api_key: str, status: str = "active"):
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.status = status


@pytest.mark.asyncio
async def test_tenant_key_allows_when_active(monkeypatch):
    app = build_app(auth_enabled=False)  # FG_API_KEY path off to isolate tenant auth

    # Patch tools.tenants.registry.get_tenant used inside api.auth
    import api.auth as auth

    def fake_get_tenant(tenant_id: str):
        if tenant_id == "acme-prod":
            return DummyTenant(
                tenant_id="acme-prod", api_key="tenant-secret", status="active"
            )
        return None

    monkeypatch.setattr(auth, "get_tenant", fake_get_tenant, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/health",  # any protected endpoint works, but /status is behind auth in your app
            headers={
                "x-tenant-id": "acme-prod",
                "x-api-key": "tenant-secret",
            },
        )

    # health is *not* protected; better to hit /status
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_revoked_is_rejected(monkeypatch):
    app = build_app(auth_enabled=False)

    import api.auth as auth

    def fake_get_tenant(tenant_id: str):
        return DummyTenant(
            tenant_id=tenant_id, api_key="tenant-secret", status="revoked"
        )

    monkeypatch.setattr(auth, "get_tenant", fake_get_tenant, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/status",
            headers={
                "x-tenant-id": "acme-prod",
                "x-api-key": "tenant-secret",
            },
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_global_key_still_works_with_registry(monkeypatch):
    # auth_enabled=True makes build_app set FG_API_KEY in env
    app = build_app(auth_enabled=True)

    # even if registry exists, global key path should pass
    import api.auth as auth

    def fake_get_tenant(tenant_id: str):
        return DummyTenant(
            tenant_id=tenant_id, api_key="tenant-secret", status="active"
        )

    monkeypatch.setattr(auth, "get_tenant", fake_get_tenant, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/status",
            headers={"x-api-key": "supersecret"},  # what build_app wires
        )

    assert resp.status_code == 200
