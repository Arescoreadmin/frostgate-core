from pathlib import Path
#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from typing import List, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

DB_URL = os.getenv("FG_DB_URL", f"sqlite:////{(Path(os.getenv('FG_STATE_DIR', '/var/lib/frostgate/state')) / 'frostgate.db').as_posix()}").strip()

IS_SQLITE = DB_URL.startswith("sqlite")
IS_POSTGRES = DB_URL.startswith("postgresql")

connect_args: dict = {}
if IS_SQLITE:
    connect_args = {
        "check_same_thread": False,
        "timeout": int(os.getenv("FG_SQLITE_TIMEOUT_SECONDS", "30")),
    }

engine: Engine = create_engine(
    DB_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

INDEXES: List[Tuple[str, str]] = [
    ("ix_decisions_created_id", "CREATE INDEX IF NOT EXISTS ix_decisions_created_id ON decisions(created_at, id)"),
    ("ix_decisions_tenant_created", "CREATE INDEX IF NOT EXISTS ix_decisions_tenant_created ON decisions(tenant_id, created_at)"),
    ("ix_decisions_event_created", "CREATE INDEX IF NOT EXISTS ix_decisions_event_created ON decisions(event_type, created_at)"),
    ("ix_decisions_threat_created", "CREATE INDEX IF NOT EXISTS ix_decisions_threat_created ON decisions(threat_level, created_at)"),
    ("ix_decisions_source_created", "CREATE INDEX IF NOT EXISTS ix_decisions_source_created ON decisions(source, created_at)"),
    ("ix_decisions_tenant_threat_created", "CREATE INDEX IF NOT EXISTS ix_decisions_tenant_threat_created ON decisions(tenant_id, threat_level, created_at)"),
]


def _detect_backend(url: str) -> str:
    u = (url or "").lower()
    if u.startswith("sqlite"):
        return "sqlite"
    if u.startswith("postgresql"):
        return "postgres"
    return "unknown"


def _list_indexes(conn, backend: str) -> list[str]:
    if backend == "sqlite":
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='decisions'")
        ).fetchall()
        return [r[0] for r in rows]

    if backend == "postgres":
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename='decisions'")
        ).fetchall()
        return [r[0] for r in rows]

    return []


def main() -> int:
    backend = _detect_backend(DB_URL)
    print(f"[apply_decisions_indexes] FG_DB_URL={DB_URL}")
    print(f"[apply_decisions_indexes] backend={backend}")

    try:
        with engine.begin() as conn:
            # Ensure table exists
            if backend == "sqlite":
                t = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'")
                ).fetchone()
                if not t:
                    print("ERROR: table 'decisions' not found. Start frostgate-core once to init_db.")
                    return 2
            elif backend == "postgres":
                t = conn.execute(text("SELECT to_regclass('public.decisions')")).fetchone()
                if not t or t[0] is None:
                    print("ERROR: table 'decisions' not found. Start frostgate-core once to init_db.")
                    return 2

            for name, ddl in INDEXES:
                print(f" - creating {name}")
                conn.execute(text(ddl))

        with engine.connect() as conn:
            existing = _list_indexes(conn, backend)
            print("[apply_decisions_indexes] indexes on decisions:")
            for n in sorted(existing):
                print(f"   - {n}")

            needed = {n for n, _ in INDEXES}
            missing = sorted(list(needed - set(existing)))
            if missing:
                print("WARNING: missing indexes:")
                for m in missing:
                    print(f"   - {m}")
                return 1

        print("[apply_decisions_indexes] done.")
        return 0

    except SQLAlchemyError as e:
        print(f"ERROR: failed applying indexes: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
