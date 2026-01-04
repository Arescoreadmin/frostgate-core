# tests/_harness.py
from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional


API_KEY_DEFAULT = "supersecret"


@contextmanager
def _temp_environ(overrides: Dict[str, str | None]) -> Iterator[None]:
    """
    Temporarily set/unset environment variables.
    Pass value None to unset.
    """
    old: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in overrides.keys()}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def build_app_factory(
    *,
    api_key: str = API_KEY_DEFAULT,
    base_sqlite_path: Optional[Path] = None,
) -> Callable[..., "object"]:
    """
    Returns a factory that rebuilds a fresh FastAPI app *without drift*.

    Usage:
      build_app = build_app_factory(...)
      app = build_app(auth_enabled=True, tmp_path=tmp_path, dev_events_enabled=True)

    Key invariants:
      - pins FG_AUTH_ENABLED explicitly (no implicit FG_API_KEY presence behavior)
      - pins FG_API_KEY
      - enables dev events only when requested
      - isolates sqlite path per test when tmp_path is provided
      - reloads api.main to avoid module-level app caching
    """

    def _factory(
        auth_enabled: bool,
        *,
        tmp_path: Optional[Path] = None,
        dev_events_enabled: bool = True,
        sqlite_name: str = "frostgate-test.db",
    ):
        sqlite_path = None
        if tmp_path is not None:
            sqlite_path = str(Path(tmp_path) / sqlite_name)
        elif base_sqlite_path is not None:
            sqlite_path = str(base_sqlite_path)

        env = {
            # pin auth behavior
            "FG_AUTH_ENABLED": "1" if auth_enabled else "0",
            "FG_API_KEY": api_key,

            # dev routes mount + seeding behavior
            "FG_DEV_EVENTS_ENABLED": "1" if dev_events_enabled else "0",
        }
        if sqlite_path:
            env["FG_SQLITE_PATH"] = sqlite_path
        else:
            # don’t let prior shells leak this into tests
            env["FG_SQLITE_PATH"] = None

        # Avoid env surprises if you ever add more knobs
        env["API_KEY"] = None

        with _temp_environ(env):
            import api.main as main
            importlib.reload(main)
            return main.build_app(auth_enabled)

    return _factory
