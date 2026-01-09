from __future__ import annotations

import os
import pytest

from api.main import build_app as _build_app
from api.db import init_db, reset_engine_cache


def _setenv(key: str, val: str) -> None:
    os.environ[str(key)] = str(val)


@pytest.fixture(autouse=True, scope="session")
def _session_env(tmp_path_factory: pytest.TempPathFactory):
    """
    Ensure a deterministic sqlite path + schema exists even for tests that call mint_key()
    before building an app.
    """
    db_path = str(tmp_path_factory.mktemp("fg-session") / "fg-session.db")
    _setenv("FG_ENV", "test")
    _setenv("FG_SQLITE_PATH", db_path)
    _setenv("FG_API_KEY", "supersecret")
    _setenv("FG_UI_TOKEN_GET_ENABLED", "1")

    # Critical: make sure schema exists in this session DB
    reset_engine_cache()
    init_db(sqlite_path=db_path)

    yield


@pytest.fixture()
def build_app(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """
    Factory fixture so tests can build an app with controlled env.
    """

    def _factory(
        auth_enabled: bool = True,
        sqlite_path: str | None = None,
        dev_events_enabled: bool = False,
        api_key: str = "supersecret",
        ui_token_get_enabled: bool = True,
    ):
        db_path = sqlite_path or str(tmp_path / "fg-test.db")

        monkeypatch.setenv("FG_SQLITE_PATH", db_path)
        monkeypatch.setenv("FG_ENV", "test")
        monkeypatch.setenv("FG_AUTH_ENABLED", "1" if auth_enabled else "0")
        monkeypatch.setenv("FG_API_KEY", api_key)
        monkeypatch.setenv("FG_DEV_EVENTS_ENABLED", "1" if dev_events_enabled else "0")
        monkeypatch.setenv(
            "FG_UI_TOKEN_GET_ENABLED", "1" if ui_token_get_enabled else "0"
        )

        reset_engine_cache()
        init_db(sqlite_path=db_path)

        return _build_app()

    return _factory
