import os
import importlib
from pathlib import Path

def test_paths_env_override(tmp_path, monkeypatch):
    st = tmp_path / "state"
    q = tmp_path / "queue"
    pc = tmp_path / "pycache"

    monkeypatch.setenv("FG_STATE_DIR", str(st))
    monkeypatch.setenv("FG_AGENT_QUEUE_DIR", str(q))
    monkeypatch.setenv("FG_PYCACHE_DIR", str(pc))

    import api.config.paths as paths
    importlib.reload(paths)

    paths.ensure_runtime_dirs()

    assert st.exists() and st.is_dir()
    assert q.exists() and q.is_dir()
    assert pc.exists() and pc.is_dir()

def test_state_dir_default_is_not_app():
    # Default should be /var/lib/frostgate/state (or env override), never /app/state.
    import api.config.paths as paths
    assert "/app/state" not in str(paths.STATE_DIR)
