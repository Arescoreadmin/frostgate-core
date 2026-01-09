from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("frostgate")


def get_engine(
    *,
    sqlite_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> Engine:
    env = os.getenv("FG_ENV", "dev").lower()

    if db_url:
        engine = create_engine(db_url, future=True)
        return engine

    pth = sqlite_path or os.getenv("FG_SQLITE_PATH")
    if not pth:
        raise RuntimeError("FG_SQLITE_PATH must be set when db_url is not provided")

    try:
        if env not in {"prod", "production"} and pth.startswith("/var/lib/"):
            if env == "test":
                raise RuntimeError(
                    f"DB path drift in test: resolved to /var/lib/... ({pth}). "
                    "Set FG_SQLITE_PATH."
                )
            log.warning(
                "DB path drift: non-prod resolved to %s. "
                "Set FG_SQLITE_PATH or fix env.",
                pth,
            )
    except Exception:
        # Never block startup on a guard
        pass

    engine = create_engine(
        f"sqlite+pysqlite:///{pth}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    return engine


def get_db(engine: Engine):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
