# api/db.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.config.paths import STATE_DIR  # tests want this symbol referenced
from api.db_models import Base

logger = logging.getLogger("frostgate")

_ENGINE: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _resolve_sqlite_path() -> Path:
    """
    Canonical sqlite contract (matches api/main.py readiness/debug):
      - If FG_SQLITE_PATH set -> use it
      - Else require FG_STATE_DIR and use $FG_STATE_DIR/frostgate.db
      - Else fallback to STATE_DIR/frostgate.db (container default)
    """
    sqlite_path = os.getenv("FG_SQLITE_PATH")
    if sqlite_path:
        return Path(sqlite_path)

    state_dir = os.getenv("FG_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "frostgate.db"

    # Container fallback (ONLY when env not supplied)
    return STATE_DIR / "frostgate.db"


def _sqlite_url() -> str:
    state_dir = os.getenv("FG_STATE_DIR", "state")  # default local ./state for dev
    p = Path(state_dir) / "frostgate.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{p}"


def _sqlite_ensure_decisions_columns(engine) -> None:
    """
    SQLite does not auto-migrate schemas. We do a minimal, safe ALTER for new MVP columns.
    """
    try:
        with engine.connect() as conn:
            # Ensure decisions table exists before pragma check
            cols = conn.exec_driver_sql("PRAGMA table_info(decisions)").fetchall()
            if not cols:
                return
            names = {row[1] for row in cols}  # (cid, name, type, notnull, dflt_value, pk)
            if "decision_diff_json" not in names:
                conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN decision_diff_json JSON")
    except Exception:
        # Never block startup due to migration attempt; logging happens elsewhere.
        return


def _db_url() -> str:
    url = os.getenv("FG_DB_URL")
    if url:
        return url
    return _sqlite_url()


def init_db() -> None:
    """
    Explicit DB init hook used by api.main startup.
    Creates engine + tables and fails loudly if misconfigured.
    """
    try:
        get_engine()
    except Exception:
        logger.exception("DB init failed")
        raise


def get_engine() -> Engine:
    global _ENGINE, _SessionLocal

    if _ENGINE is None:
        url = _db_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _ENGINE = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)

        # Ensure schema
        Base.metadata.create_all(bind=_ENGINE)
        _sqlite_ensure_decisions_columns(_ENGINE)


        logger.warning("DB_ENGINE=%s", url)
        if url.startswith("sqlite"):
            logger.warning("SQLITE_PATH=%s", _resolve_sqlite_path().as_posix())

    return _ENGINE


def get_db() -> Generator[Session, None, None]:
    global _SessionLocal
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None

    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
SessionLocal = _SessionLocal

