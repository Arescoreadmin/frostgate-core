import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import create_engine

from api.db.api_keys_store import insert_api_key


def utcnow():
    return datetime.now(timezone.utc)


def main():
    prefix = os.getenv("FG_MINT_PREFIX", "ADMIN").strip().upper()
    scopes = os.getenv("FG_MINT_SCOPES", "feed:read,ingest:write,decisions:read").strip()
    name = os.getenv("FG_MINT_NAME", f"{prefix.lower()}-{utcnow().isoformat()}").strip()

    if not prefix:
        raise SystemExit("FG_MINT_PREFIX cannot be empty")

    raw = f"{prefix}_" + secrets.token_urlsafe(32)

    db_url = os.getenv("FG_DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Missing FG_DB_URL (or DATABASE_URL) for minting")

    engine = create_engine(db_url)

    insert_api_key(
        engine,
        name=name,
        raw_key=raw,
        scopes=scopes,
        enabled=True,
    )

    print(raw)  # print only once


if __name__ == "__main__":
    main()
