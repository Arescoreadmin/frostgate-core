"""Test configuration for Frostgate backend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def anyio_backend() -> str:
    """Force pytest-anyio to run using asyncio only."""

    return "asyncio"
