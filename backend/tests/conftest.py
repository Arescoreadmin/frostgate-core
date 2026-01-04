from __future__ import annotations

import pytest
from pathlib import Path

from tests._harness import build_app_factory


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def build_app(tmp_path: Path):
    """
    Fixture returns a callable:
        app = build_app(auth_enabled=True, api_key="supersecret", dev_events_enabled=True)
    """
    return build_app_factory(tmp_path)
