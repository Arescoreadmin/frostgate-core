import os
import httpx
import pytest


def _ci_mode() -> bool:
    return (
        os.getenv("CI") == "true" or os.getenv("CI") == "1" or os.getenv("FG_CI") == "1"
    )


def _get(url: str) -> httpx.Response:
    try:
        return httpx.get(url, timeout=5.0)
    except httpx.ConnectError:
        if _ci_mode():
            raise
        pytest.skip(f"Server not reachable at {url}. Start it with `make itest-up`.")
