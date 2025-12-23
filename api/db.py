# api/db.py
from __future__ import annotations

import os
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.config.paths import STATE_DIR  # <-- tests want this symbol referenced
from api.db_models import Base


_ENGINE: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _sqlite_url() -> str:
    # ensure state dir exists
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(STATE_DIR / 'frostgate.sqlite3').as_posix()}"


def _db_url() -> str:
    url = os.getenv("FG_DB_URL")
    if url:
        return url
    return _sqlite_url()


def get_engine() -> Engine:
    global _ENGINE, _SessionLocal
    if _ENGINE is None:
        url = _db_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _ENGINE = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)
        Base.metadata.create_all(bind=_ENGINE)
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
