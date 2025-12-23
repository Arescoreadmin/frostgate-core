from __future__ import annotations

import os
from pathlib import Path

# IMPORTANT: this runs at import time (before api.db is imported by tests)
BASE = Path(os.getenv("PYTEST_TMP_BASE", "/tmp")) / "frostgate_pytest"
STATE = BASE / "state"
QUEUE = BASE / "agent_queue"
PYCACHE = BASE / "pycache"

for d in (STATE, QUEUE, PYCACHE):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("FG_ENV", "dev")

# Force db to writable location for tests (bypasses /var/lib defaults)
os.environ["FG_STATE_DIR"] = str(STATE)
os.environ["FG_DB_URL"] = f"sqlite:///{(STATE / 'frostgate.db').as_posix()}"

# Agent/runtime dirs
os.environ["FG_AGENT_QUEUE_DIR"] = str(QUEUE)
os.environ["FG_PYCACHE_DIR"] = str(PYCACHE)
os.environ.pop("FG_AGENT_QUEUE_PATH", None)
