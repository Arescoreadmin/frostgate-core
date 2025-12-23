#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Iterable, Tuple

from api.db import init_db, get_db
from api.db_models import ApiKey, hash_api_key


def _raw_key(s: str) -> str:
    """
    Accept either raw key or "RAW|scopes".
    We only want RAW.
    """
    return (s or "").strip().split("|", 1)[0].strip()


def _pairs_from_env() -> Iterable[Tuple[str, str]]:
    """
    Prefer explicit seed vars; fall back to FG_ADMIN_KEY/FG_AGENT_KEY.
    Scopes are pinned here, not in .env strings.
    """
    admin = _raw_key(os.getenv("FG_ADMIN_KEY", ""))
    agent = _raw_key(os.getenv("FG_AGENT_KEY", ""))

    if not admin or not agent:
        raise SystemExit("Missing FG_ADMIN_KEY and/or FG_AGENT_KEY in env.")

    return [
        (admin, "decisions:read,defend:write,ingest:write"),
        (agent, "decisions:read,ingest:write"),
    ]


def _prefix(raw: str) -> str:
    return raw.split("_", 1)[0] + "_"


def upsert_key(raw: str, scopes_csv: str) -> None:
    init_db()
    for db in get_db():
        prefix = _prefix(raw)
        key_h = hash_api_key(raw)

        row = db.query(ApiKey).filter(ApiKey.key_hash == key_h).first()
        if row:
            # exact key already exists; just ensure enabled/scopes
            row.enabled = True
            row.scopes_csv = scopes_csv
            db.add(row)
            db.commit()
            print(f"ok existing key_hash match prefix={row.prefix} scopes={row.scopes_csv}")
            return

        # else: upsert by prefix (ONLY safe here during seeding)
        row = db.query(ApiKey).filter(ApiKey.prefix == prefix).first()
        if not row:
            row = ApiKey(prefix=prefix, key_hash=key_h, scopes_csv=scopes_csv, enabled=True)
        else:
            row.key_hash = key_h
            row.scopes_csv = scopes_csv
            row.enabled = True

        db.add(row)
        db.commit()
        print(f"ok upserted prefix={prefix} scopes={scopes_csv}")
        return


def main() -> None:
    for raw, scopes in _pairs_from_env():
        upsert_key(raw, scopes)


if __name__ == "__main__":
    main()
