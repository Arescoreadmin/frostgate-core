from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

# Keep settings extremely boring and predictable.
# Anything path-related should come from api.config.paths to ensure one source of truth.
from .paths import STATE_DIR, AGENT_QUEUE_DIR, PYCACHE_DIR


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("FG_ENV", os.getenv("ENV", "dev")).strip() or "dev"
    db_url: str = os.getenv("FG_DB_URL", "").strip()

    # Canonical runtime dirs (already centralized in paths.py)
    state_dir: Path = STATE_DIR
    agent_queue_dir: Path = AGENT_QUEUE_DIR
    pycache_dir: Path = PYCACHE_DIR

    # Optional knobs
    log_level: str = os.getenv("FG_LOG_LEVEL", "INFO").strip().upper()


settings = Settings()
