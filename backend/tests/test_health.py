"""Smoke tests for the FastAPI scaffold."""

import pytest

from app.api import routes
from app.main import root


@pytest.mark.anyio("asyncio")
async def test_root_returns_message() -> None:
    assert await root() == {"message": "Frostgate backend is online"}


@pytest.mark.anyio("asyncio")
async def test_health_endpoint_reports_ok() -> None:
    assert await routes.health() == {"status": "ok"}
