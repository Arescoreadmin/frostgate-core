import pytest
from httpx import AsyncClient

from tests.test_auth import build_app  # reuse the helper so FG_API_KEY is set


@pytest.mark.asyncio
async def test_guardian_disruption_limit_and_roe_flags():
    """
    With persona=guardian + SECRET + high failed_auths:
      - /v1/defend should:
        - return at most 1 block_ip mitigation (guardian cap)
        - mark explain.roe_applied = True
        - surface tie_d + persona + classification in explain
    """
    app = build_app(auth_enabled=True)

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(
            "/v1/defend",
            headers={"x-api-key": "supersecret"},
            json={
                "source": "edge-gateway-1",
                "tenant_id": "tenant-doctrine-guardian",
                "timestamp": "2025-11-17T00:00:00Z",
                "classification": "SECRET",
                "persona": "guardian",
                "payload": {
                    "event_type": "auth",
                    "src_ip": "192.0.2.10",
                    "failed_auths": 12,
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()

    # Basic shape
    assert data["threat_level"] in ("medium", "high")
    assert "mitigations" in data
    assert "explain" in data

    actions = [m["action"] for m in data["mitigations"]]
    # Guardian shouldn't go nuclear: cap block_ip actions
    assert actions.count("block_ip") <= 1

    explain = data["explain"]

    # ROE & doctrine fields should be present and sane
    assert explain.get("roe_applied") is True
    assert explain.get("disruption_limited") in (True, False)
    assert explain.get("ao_required") in (True, False)

    assert explain.get("persona") == "guardian"
    assert explain.get("classification") == "SECRET"

    tie_d = explain.get("tie_d")
    assert tie_d is not None
    assert 0.0 <= tie_d["service_impact"] <= 1.0
    assert 0.0 <= tie_d["user_impact"] <= 1.0
    assert tie_d["gating_decision"] in ("allow", "require_approval", "reject")


@pytest.mark.asyncio
async def test_sentinel_can_allow_more_disruption():
    """
    Sentinel persona is allowed to be more aggressive than guardian.
    We don't hard-assert exact numbers, but we ensure:
      - same scenario with sentinel does NOT have *stricter* mitigations
        than guardian in terms of block_ip count.
    """
    app = build_app(auth_enabled=True)

    async with AsyncClient(app=app, base_url="http://test") as client:
        base_payload = {
            "source": "edge-gateway-1",
            "tenant_id": "tenant-doctrine-compare",
            "timestamp": "2025-11-17T00:00:00Z",
            "classification": "SECRET",
            "payload": {
                "event_type": "auth",
                "src_ip": "192.0.2.11",
                "failed_auths": 12,
            },
        }

        guardian_resp = await client.post(
            "/v1/defend",
            headers={"x-api-key": "supersecret"},
            json={**base_payload, "persona": "guardian"},
        )
        sentinel_resp = await client.post(
            "/v1/defend",
            headers={"x-api-key": "supersecret"},
            json={**base_payload, "persona": "sentinel"},
        )

    assert guardian_resp.status_code == 200
    assert sentinel_resp.status_code == 200

    guardian_actions = [m["action"] for m in guardian_resp.json()["mitigations"]]
    sentinel_actions = [m["action"] for m in sentinel_resp.json()["mitigations"]]

    guardian_blocks = guardian_actions.count("block_ip")
    sentinel_blocks = sentinel_actions.count("block_ip")

    # Sentinel should not be *less* willing to block than guardian in same scenario.
    assert sentinel_blocks >= guardian_blocks
