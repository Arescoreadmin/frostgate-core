# scripts/create_api_key.py
from __future__ import annotations

import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


def _add_repo_paths() -> None:
    repo = Path(__file__).resolve().parents[1]

    # Candidates where the `api/` package might live
    candidates = [
        repo,                    # ./api
        repo / "src",            # ./src/api
        repo / "backend",        # ./backend/api
        repo / "app",            # ./app/api (some people do this)
        repo / "server",         # ./server/api
        repo / "services",       # ./services/* (less likely)
    ]

    for base in candidates:
        if (base / "api" / "__init__.py").exists() or (base / "api").is_dir():
            if str(base) not in sys.path:
                sys.path.insert(0, str(base))
            return

    # If we get here, no `api` dir was found in expected places.
    sys.stderr.write(
        "\n[create_api_key] ERROR: Could not locate an 'api' package.\n"
        f"Repo root assumed: {repo}\n"
        "Searched for api/ in:\n"
        + "".join(f"  - {p}\n" for p in candidates)
        + "\nFix: put this script next to the repo root that contains api/, "
          "or add the correct directory to candidates.\n\n"
    )
    sys.exit(1)


_add_repo_paths()

# Now imports should work if the repo layout is sane
from sqlalchemy.orm import Session  # noqa: E402
from api.db import SessionLocal  # noqa: E402
from api.db_models import ApiKey, hash_api_key  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python scripts/create_api_key.py <PREFIX> <scopes_csv> [name]")
        print('example: python scripts/create_api_key.py ADMIN "decisions:read,defend:write,ingest:write" "Admin key"')
        raise SystemExit(2)

    prefix_in = sys.argv[1].strip().upper()
    scopes_csv = sys.argv[2].strip()
    name = sys.argv[3].strip() if len(sys.argv) > 3 else None

    prefix = prefix_in.replace("_", "") + "_"  # enforce "ADMIN_"

    raw = prefix + secrets.token_urlsafe(32)
    key_hash = hash_api_key(raw)

    db: Session = SessionLocal()
    try:
        row = ApiKey(
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            scopes_csv=scopes_csv,
            enabled=True,
            created_at=utcnow(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    print("\nNEW_API_KEY=" + raw)
    print("prefix=" + prefix)
    print("scopes=" + scopes_csv)


if __name__ == "__main__":
    main()
