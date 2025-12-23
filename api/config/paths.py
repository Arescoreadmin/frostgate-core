from __future__ import annotations

import os
from pathlib import Path

def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).resolve()

STATE_DIR: Path = _env_path("FG_STATE_DIR", "/var/lib/frostgate/state")
AGENT_QUEUE_DIR: Path = _env_path("FG_AGENT_QUEUE_DIR", "/var/lib/frostgate/agent_queue")
PYCACHE_DIR: Path = _env_path("FG_PYCACHE_DIR", "/var/lib/frostgate/pycache")

def ensure_runtime_dirs() -> None:
    for d in (STATE_DIR, AGENT_QUEUE_DIR, PYCACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
