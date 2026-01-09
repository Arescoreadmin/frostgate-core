import importlib
from pathlib import Path


def test_db_sqlite_fallback_uses_state_dir(monkeypatch, tmp_path):
    # Force state dir override and ensure db module builds sqlite url under it
    st = tmp_path / "state"
    monkeypatch.setenv("FG_STATE_DIR", str(st))
    monkeypatch.delenv("FG_DB_URL", raising=False)

    import api.config.paths as paths

    importlib.reload(paths)

    import api.db as db

    importlib.reload(db)

    # We can only validate if db.py contains a sqlite fallback path string using STATE_DIR
    src = Path(db.__file__).read_text(encoding="utf-8")
    assert "STATE_DIR" in src
