from __future__ import annotations

from pathlib import Path

from api.db import _resolve_sqlite_path


def test_sqlite_path_default_is_repo_local_in_test_env(monkeypatch):
    monkeypatch.delenv("FG_SQLITE_PATH", raising=False)
    monkeypatch.delenv("FG_STATE_DIR", raising=False)
    monkeypatch.setenv("FG_ENV", "test")

    p = _resolve_sqlite_path()
    assert str(p).startswith(str(Path.cwd())), f"Expected repo-local path, got: {p}"
    assert "/var/lib/" not in str(p)


def test_sqlite_path_prod_defaults_to_var_lib(monkeypatch):
    monkeypatch.delenv("FG_SQLITE_PATH", raising=False)
    monkeypatch.delenv("FG_STATE_DIR", raising=False)
    monkeypatch.setenv("FG_ENV", "production")

    p = _resolve_sqlite_path()
    assert str(p) == "/var/lib/frostgate/state/frostgate.db"
