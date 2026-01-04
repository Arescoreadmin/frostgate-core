from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Callable, Any

DEFAULT_API_KEY = "supersecret"


def _b(v: bool) -> str:
    """Make boolean env vars deterministic."""
    return "1" if bool(v) else "0"


def _set_env(tmp_path: Path, *, api_key: str, auth_enabled: bool, dev_events_enabled: bool) -> None:
    """
    Centralized, deterministic test env.
    Tests MUST NOT rely on user's shell env.
    """
    os.environ["FG_API_KEY"] = api_key
    os.environ["FG_AUTH_ENABLED"] = _b(auth_enabled)
    os.environ["FG_DEV_EVENTS_ENABLED"] = _b(dev_events_enabled)
    os.environ["FG_SQLITE_PATH"] = str(tmp_path / "frostgate-test.db")


def _call_build_app(build_app_fn: Callable[..., Any], *, test_mode: bool, auth_enabled: bool) -> Any:
    """
    Call api.main.build_app in a signature-tolerant way.
    We prefer explicit kwargs when available.
    """
    sig = inspect.signature(build_app_fn)
    params = sig.parameters

    kwargs: dict[str, Any] = {}

    # Support build_app(test_mode=True/False)
    if "test_mode" in params:
        kwargs["test_mode"] = test_mode

    # Support build_app(auth_enabled=...)
    if "auth_enabled" in params:
        kwargs["auth_enabled"] = auth_enabled

    # Most common modern shape: build_app(test_mode=..., auth_enabled=...)
    if kwargs:
        return build_app_fn(**kwargs)

    # Legacy shape: build_app(test_mode_bool) only
    # (Auth should still be driven by FG_AUTH_ENABLED / FG_API_KEY env.)
    return build_app_fn(test_mode)


def build_app_factory(tmp_path: Path) -> Callable[..., Any]:
    """
    Returns a callable:
        app = build_app(auth_enabled=True, api_key="supersecret", dev_events_enabled=True)
    """

    def _build(
        auth_enabled: bool = True,
        *,
        api_key: str = DEFAULT_API_KEY,
        dev_events_enabled: bool = True,
    ):
        _set_env(tmp_path, api_key=api_key, auth_enabled=auth_enabled, dev_events_enabled=dev_events_enabled)

        import api.main as main
        importlib.reload(main)

        return _call_build_app(main.build_app, test_mode=True, auth_enabled=auth_enabled)

    return _build
