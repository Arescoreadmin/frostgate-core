#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(".").resolve()

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def patch_engine_rules_payload_alias():
    p = ROOT / "engine" / "rules.py"
    s = read(p)

    # dict path: payload = telemetry.get("payload") or {}
    s, n1 = re.subn(
        r'payload\s*=\s*telemetry\.get\("payload"\)\s*or\s*\{\}',
        'payload = telemetry.get("payload") or telemetry.get("event") or {}',
        s,
        flags=re.MULTILINE,
    )

    # model path: payload = telemetry.payload
    s, n2 = re.subn(
        r'payload\s*=\s*telemetry\.payload',
        'payload = getattr(telemetry, "payload", None) or getattr(telemetry, "event", None) or {}',
        s,
        flags=re.MULTILINE,
    )

    if n1 or n2:
        write(p, s)
        print(f"[patch] engine/rules.py: payload aliasing added (dict:{n1}, model:{n2})")
    else:
        print("[skip] engine/rules.py: payload aliasing already looks fine")

def ensure_feed_router():
    feed = ROOT / "api" / "feed.py"
    if not feed.exists():
        write(feed, '''from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from api.auth_scopes import require_api_key
from api.db import get_db

log = logging.getLogger("frostgate.feed")

router = APIRouter(prefix="/feed", tags=["feed"])

@router.get(
    "/live",
    dependencies=[Depends(require_api_key())],
)
def feed_live(limit: int = 50, db: Session = Depends(get_db)) -> Dict[str, Any]:
    # MVP stub: return empty stream; later wire redis/pubsub.
    limit = max(1, min(int(limit or 50), 500))
    return {"items": [], "limit": limit}
''')
        print("[patch] api/feed.py: created /feed/live stub (auth protected)")
    else:
        print("[skip] api/feed.py exists")

    main = ROOT / "api" / "main.py"
    ms = read(main)

    # add import if missing
    if "from api.feed import router as feed_router" not in ms:
        # insert near other router imports
        ms = re.sub(
            r"(from api\.\w+\s+import\s+router\s+as\s+\w+_router\s*\n)+",
            lambda m: m.group(0) + "from api.feed import router as feed_router\n",
            ms,
            count=1,
            flags=re.MULTILINE,
        )

    # include router if missing
    if "app.include_router(feed_router)" not in ms:
        # include after ingest/decisions
        ms = re.sub(
            r"(app\.include_router\(ingest_router\)\s*\n)",
            r"\1app.include_router(feed_router)\n",
            ms,
            count=1,
            flags=re.MULTILINE,
        )

    write(main, ms)
    print("[patch] api/main.py: ensured feed_router import + include")

def patch_api_auth_to_accept_db_keys():
    """
    Your /defend test is 403 because something is using env-key-only validation.
    Patch api/auth.py to accept either:
      - env expected key (legacy)
      - OR a DB ApiKey row hashed match (modern)
    """
    p = ROOT / "api" / "auth.py"
    if not p.exists():
        print("[skip] api/auth.py not found")
        return

    s = read(p)

    # If already patched, skip
    if "verify_api_key_raw" in s or "api.auth_scopes" in s:
        print("[skip] api/auth.py already appears DB-aware")
        return

    # Ensure imports for DB-aware auth
    if "from api.auth_scopes import verify_api_key_raw" not in s:
        s = re.sub(
            r"(^from fastapi[^\n]*\n)",
            r"\1from api.auth_scopes import verify_api_key_raw\nfrom api.db import get_db\n",
            s,
            count=1,
            flags=re.MULTILINE,
        )
    if "from sqlalchemy.orm import Session" not in s:
        s = re.sub(
            r"(^from api\.\w+[^\n]*\n)",
            r"\1from sqlalchemy.orm import Session\n",
            s,
            count=1,
            flags=re.MULTILINE,
        )

    # Patch verify_api_key signature to accept db session
    s = re.sub(
        r"def\s+verify_api_key\(\s*x_api_key:.*?\):",
        "def verify_api_key(x_api_key=None, db: Session = Depends(get_db)):",
        s,
        flags=re.DOTALL,
    )

    # Replace the body logic with: env check then db fallback
    # Find function block crudely from def verify_api_key to next blank line after it ends (best effort)
    m = re.search(r"def\s+verify_api_key\(.*?\):\n(    .*\n)+", s)
    if not m:
        print("[warn] could not locate verify_api_key block cleanly; manual patch may be needed")
        write(p, s)
        return

    # Build a new implementation block
    new_impl = """def verify_api_key(x_api_key=None, db: Session = Depends(get_db)) -> None:
    expected = _get_expected_api_key()

    if x_api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    raw = str(x_api_key).strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    # 1) Legacy env key path
    if expected and raw == expected:
        return

    # 2) DB-backed key path (hash lookup)
    try:
        verify_api_key_raw(raw_key=raw, db=db, required_scopes=None)
        return
    except HTTPException:
        # keep the contract: invalid => 403
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
"""
    # Replace only the first occurrence of the def block
    s = re.sub(r"def\s+verify_api_key\(.*?\):\n(?:    .*\n)+", new_impl + "\n", s, count=1)

    write(p, s)
    print("[patch] api/auth.py: verify_api_key now accepts env OR DB keys")

def patch_scripts_test_ingest_persists():
    """
    Fix failure:
      psycopg.errors.UndefinedColumn: column "key" of relation "api_keys" does not exist
    So rewrite scripts/test_ingest_persists.py helper to create ApiKey via ORM columns dynamically.
    """
    p = ROOT / "scripts" / "test_ingest_persists.py"
    if not p.exists():
        print("[skip] scripts/test_ingest_persists.py not found")
        return

    s = read(p)

    if "_create_api_key" not in s:
        print("[skip] scripts/test_ingest_persists.py: no _create_api_key found")
        return

    # Replace _create_api_key function with dynamic ORM insert
    new_fn = r'''
def _create_api_key(db):
    """
    Create an API key row compatible with current ApiKey ORM/table schema.
    This avoids hardcoding columns like (key, scopes, is_active) which may not exist.
    """
    import uuid
    from api.db_models import ApiKey, hash_api_key

    raw = f"TEST_{uuid.uuid4().hex}"
    cols = set(ApiKey.__table__.columns.keys())

    kwargs = {}
    if "key_hash" in cols:
        kwargs["key_hash"] = hash_api_key(raw)
    if "prefix" in cols:
        kwargs["prefix"] = raw.split("_", 1)[0] + "_" if "_" in raw else (raw[:8] + "_")
    if "scopes_csv" in cols:
        kwargs["scopes_csv"] = "ingest:write"
    if "enabled" in cols:
        kwargs["enabled"] = True
    if "is_active" in cols:
        kwargs["is_active"] = True

    row = ApiKey(**kwargs)
    db.add(row)
    db.commit()
    return raw
'''

    # crude function replacement
    s2, n = re.subn(
        r"def\s+_create_api_key\s*\(.*?\)\s*:\n(?:^[ \t].*\n)+?(?=^\S|\Z)",
        new_fn.strip() + "\n\n",
        s,
        flags=re.MULTILINE,
    )

    if n == 0:
        print("[warn] scripts/test_ingest_persists.py: could not replace _create_api_key cleanly")
        return

    write(p, s2)
    print("[patch] scripts/test_ingest_persists.py: _create_api_key now uses ORM columns dynamically")

def remove_duplicate_telemetryinput_in_main():
    main = ROOT / "api" / "main.py"
    s = read(main)

    # If api/main.py defines TelemetryInput, delete that class and rely on api.schemas.TelemetryInput
    # This version is more robust: remove from class TelemetryInput to next class OR router definition line.
    s2, n = re.subn(
        r"^class\s+TelemetryInput\s*\(BaseModel\)\s*:\n(?:^[ \t].*\n)+?(?=^(class\s|router\s*=|app\s*=|def\s)|\Z)",
        "",
        s,
        flags=re.MULTILINE,
    )
    if n:
        # Ensure import
        if "from api.schemas import TelemetryInput" not in s2:
            s2 = re.sub(r"^from api\.schemas import .*?\n", lambda m: m.group(0), s2, flags=re.MULTILINE)
            # insert after other api imports
            s2 = re.sub(
                r"(^from api\.[^\n]+\n)+",
                lambda m: m.group(0) + "from api.schemas import TelemetryInput\n",
                s2,
                count=1,
                flags=re.MULTILINE,
            )
        write(main, s2)
        print("[patch] api/main.py: removed duplicate TelemetryInput class")
    else:
        print("[skip] api/main.py: no duplicate TelemetryInput block matched")

def main():
    print("== patch_failing_tests starting ==")
    patch_scripts_test_ingest_persists()
    patch_api_auth_to_accept_db_keys()
    patch_engine_rules_payload_alias()
    ensure_feed_router()
    remove_duplicate_telemetryinput_in_main()
    print("== patch_failing_tests done ==")

if __name__ == "__main__":
    main()
