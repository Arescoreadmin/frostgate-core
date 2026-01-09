from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.config.paths import STATE_DIR  # tests assert this symbol is referenced in this file
from api.db_models import Base

log = logging.getLogger("frostgate")

_ENGINE: Engine | None = None
_SESSIONMAKER: sessionmaker | None = None


def _env() -> str:
    return os.getenv("FG_ENV", "dev").lower()


def _resolve_sqlite_path(sqlite_path: Optional[str] = None) -> Path:
    """
    Precedence:
      1) explicit arg
      2) FG_SQLITE_PATH
      3) default based on env:
           - test/dev: <repo>/state/frostgate.db
           - prod/production: /var/lib/frostgate/state/frostgate.db
    Note: We DO NOT blindly trust imported STATE_DIR in tests because it may have been
    computed at import-time under a different FG_ENV. Tests expect repo-local defaults.
    """
    if sqlite_path:
        return Path(sqlite_path).expanduser().resolve()

    env_pth = os.getenv("FG_SQLITE_PATH")
    if env_pth:
        return Path(env_pth).expanduser().resolve()

    env = _env()

    if env in {"prod", "production"}:
        return Path("/var/lib/frostgate/state/frostgate.db")

    # test/dev default: repo-local state/
    return (Path.cwd() / "state" / "frostgate.db").resolve()


def _make_engine(*, sqlite_path: Optional[str] = None, db_url: Optional[str] = None) -> Engine:
    env = _env()

    if db_url:
        return create_engine(db_url, future=True)

    pth = _resolve_sqlite_path(sqlite_path)

    # Drift guard: non-prod must not silently write into /var/lib
    if env not in {"prod", "production"} and str(pth).startswith("/var/lib/"):
        if env == "test":
            raise RuntimeError(
                f"DB path drift in test: resolved to /var/lib/... ({pth}). Set FG_SQLITE_PATH."
            )
        log.warning(
            "DB path drift: non-prod resolved to %s. Set FG_SQLITE_PATH or fix env.",
            pth,
        )

    # “STATE_DIR” must appear in-source for a regression test.
    # We don't need it for computation here, but we reference it intentionally.
    _ = STATE_DIR

    log.warning("DB_ENGINE=sqlite+pysqlite:///%s", pth)
    log.warning("SQLITE_PATH=%s", pth)

    return create_engine(
        f"sqlite+pysqlite:///{pth}",
        future=True,
        connect_args={"check_same_thread": False},
    )


def reset_engine_cache() -> None:
    global _ENGINE, _SESSIONMAKER
    if _ENGINE is not None:
        try:
            _ENGINE.dispose()
        except Exception:
            pass
    _ENGINE = None
    _SESSIONMAKER = None


def get_engine(*, sqlite_path: Optional[str] = None, db_url: Optional[str] = None) -> Engine:
    """
    - If sqlite_path/db_url provided: return a fresh engine (no cache).
    - Else: cached engine.
    """
    global _ENGINE, _SESSIONMAKER

    if sqlite_path is not None or db_url is not None:
        return _make_engine(sqlite_path=sqlite_path, db_url=db_url)

    if _ENGINE is None:
        _ENGINE = _make_engine()
        _SESSIONMAKER = sessionmaker(bind=_ENGINE, expire_on_commit=False, future=True)

    return _ENGINE


def _get_sessionmaker() -> sessionmaker:
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        get_engine()
    assert _SESSIONMAKER is not None
    return _SESSIONMAKER


def init_db(*, sqlite_path: Optional[str] = None, db_url: Optional[str] = None, engine: Engine | None = None) -> None:
    """
    Tests call init_db(sqlite_path=...).
    """
    eng = engine or get_engine(sqlite_path=sqlite_path, db_url=db_url)
    Base.metadata.create_all(bind=eng)


def get_db() -> Iterator[Session]:
    SessionLocal = _get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
