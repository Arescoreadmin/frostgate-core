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
    Canonical sqlite contract:
      - If FG_SQLITE_PATH set -> use it (absolute or relative)
      - Else if FG_STATE_DIR set -> use $FG_STATE_DIR/frostgate.db
      - Else fallback to STATE_DIR/frostgate.db (container default)
    """
    sqlite_path = (os.getenv("FG_SQLITE_PATH") or "").strip()
    if sqlite_path:
        return Path(sqlite_path)

    state_dir = (os.getenv("FG_STATE_DIR") or "").strip()
    if state_dir:
        return Path(state_dir) / "frostgate.db"

    return STATE_DIR / "frostgate.db"


def _sqlite_url_from_path(p: Path) -> str:
    p = p.expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    # SQLAlchemy sqlite URL wants 3 slashes for absolute paths; for relative, it still works.
    return f"sqlite+pysqlite:///{p.as_posix()}"


def _db_url() -> str:
    """
    If FG_DB_URL is set, trust it (postgres etc).
    Otherwise build sqlite URL from canonical sqlite path contract.
    """
    url = (os.getenv("FG_DB_URL") or "").strip()
    if url:
        return url

    return _sqlite_url_from_path(_resolve_sqlite_path())


def _sqlite_ensure_decisions_columns(engine: Engine) -> None:
    """
    SQLite does not auto-migrate schemas. We do a minimal, safe ALTER for new MVP columns.
    Never blocks startup if anything goes wrong (logs at debug).
    """
    try:
        with engine.begin() as conn:
            cols = conn.exec_driver_sql("PRAGMA table_info(decisions)").fetchall()
            if not cols:
                return

            names = {row[1] for row in cols}  # (cid, name, type, notnull, dflt_value, pk)

            # SQLite doesn't have a real JSON type; TEXT is fine for MVP.
            if "decision_diff_json" not in names:
                conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN decision_diff_json TEXT")
    except Exception as e:
        logger.debug("sqlite micro-migration skipped/failed: %s", e)


def init_db() -> None:
    """
    Explicit DB init hook used by api.main startup and tests.
    Creates engine + tables (and sqlite micro-migrations).
    Fail loudly if misconfigured.
    """
    engine = get_engine()

    # Ensure schema (idempotent)
    Base.metadata.create_all(bind=engine)

    # SQLite micro-migration
    if str(engine.url).startswith("sqlite"):
        _sqlite_ensure_decisions_columns(engine)


def get_engine() -> Engine:
    global _ENGINE, _SessionLocal

    if _ENGINE is None:
        url = _db_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}

        _ENGINE = create_engine(
            url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)

        logger.warning("DB_ENGINE=%s", url)
        if url.startswith("sqlite"):
            logger.warning("SQLITE_PATH=%s", _resolve_sqlite_path().expanduser().as_posix())

    return _ENGINE


def get_sessionmaker() -> sessionmaker:
    """
    Preferred way for internal callers/tests to grab the configured sessionmaker.
    """
    global _SessionLocal
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    db: Session = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


# Backwards-compat symbol if anything imports it, but DO NOT rely on it at import time.
SessionLocal = _SessionLocal
