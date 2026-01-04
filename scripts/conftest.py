import os
import sqlite3
import pytest

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()

@pytest.fixture(scope="session")
def base_url() -> str:
    return _env("BASE_URL", DEFAULT_BASE_URL)  # type: ignore[return-value]

@pytest.fixture(scope="session")
def api_key() -> str:
    # prefer FG_API_KEY, fallback to API_KEY
    v = _env("FG_API_KEY") or _env("API_KEY") or "supersecret"
    return v

@pytest.fixture(scope="session")
def sqlite_path() -> str:
    p = _env("FG_SQLITE_PATH")
    if not p:
        # don’t guess silently; tests should be deterministic
        raise RuntimeError("FG_SQLITE_PATH is required for tests (path to frostgate.db)")
    return p

@pytest.fixture()
def clear_decisions(sqlite_path: str):
    """
    Hard reset only the decisions table between tests.
    Uses sqlite directly for speed and determinism.
    """
    con = sqlite3.connect(sqlite_path)
    try:
        con.execute("DELETE FROM decisions;")
        con.commit()
    finally:
        con.close()
    yield
