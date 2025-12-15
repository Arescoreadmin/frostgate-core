from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("FG_DB_URL", "sqlite:///./frostgate_decisions.db").strip()

# ---- Engine tuning defaults ----
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

connect_args: dict = {}

if IS_SQLITE:
    # SQLite + FastAPI + threads
    connect_args = {
        "check_same_thread": False,
        "timeout": int(os.getenv("FG_SQLITE_TIMEOUT_SECONDS", "30")),
    }

# Pool sizing: sane defaults, override via env in prod.
POOL_SIZE = int(os.getenv("FG_DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("FG_DB_MAX_OVERFLOW", "10"))
POOL_RECYCLE = int(os.getenv("FG_DB_POOL_RECYCLE_SECONDS", "1800"))  # 30 min
POOL_TIMEOUT = int(os.getenv("FG_DB_POOL_TIMEOUT_SECONDS", "30"))

engine: Engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=POOL_RECYCLE,
    pool_size=0 if IS_SQLITE else POOL_SIZE,
    max_overflow=0 if IS_SQLITE else MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT if not IS_SQLITE else None,
    future=True,
)

# ---- SQLite pragmas to reduce "database is locked" ----
if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _conn_record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.execute("PRAGMA busy_timeout=30000;")  # ms
            # Helps read concurrency a bit in WAL
            cur.execute("PRAGMA read_uncommitted=1;")
        finally:
            cur.close()

# ---- Postgres safety knobs ----
if IS_POSTGRES:

    @event.listens_for(engine, "connect")
    def _postgres_session_settings(dbapi_conn, _conn_record):
        # Keep this minimal. If you want more, do it via DB config.
        with dbapi_conn.cursor() as cur:
            # Prevent "hung forever" connections in prod.
            statement_timeout_ms = int(os.getenv("FG_PG_STATEMENT_TIMEOUT_MS", "5000"))
            cur.execute(f"SET statement_timeout = {statement_timeout_ms};")

            # Force UTC at the session level.
            cur.execute("SET TIME ZONE 'UTC';")


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # avoids lazy-load surprises after commit
    bind=engine,
    future=True,
)

Base = declarative_base()


def init_db() -> None:
    # Import models so Base.metadata is populated
    from api import db_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_ping() -> bool:
    """Quick DB liveness check for readiness probes (optional use)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
