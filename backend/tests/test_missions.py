"""Tests for the missions endpoint."""

import pytest

from app.api.routes import missions


@pytest.mark.anyio("asyncio")
async def test_missions_returns_seed_data() -> None:
    payload = await missions()
    assert isinstance(payload, list)
    assert {mission.id for mission in payload} == {
        "ops-001",
        "ops-002",
        "ops-003",
    }
