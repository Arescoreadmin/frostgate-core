# api/db.py
from __future__ import annotations

import os
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.db_models import Base

_ENGINE = None
_SessionLocal = None


def _db_url() -> str:
    url = os.getenv("FG_DB_URL", "").strip()
    if not url:
        # Fallback for dev only; in prod you set FG_DB_URL.
        # NOTE: sqlite path is under /app/state if you want a fallback.
        url = "sqlite:////app/state/frostgate.db"
    return url


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            _db_url(),
            pool_pre_ping=True,
            future=True,
        )
    return _ENGINE


def _get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(),
            autocommit=False,
            autoflush=False,
            future=True,
        )
    return _SessionLocal


def init_db() -> None:
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)


def db_ping() -> bool:
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


def get_db() -> Generator[Session, None, None]:
    SessionLocal = _get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
