#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

p = Path("api/auth.py")
s = p.read_text(encoding="utf-8")

# Ensure imports exist
need = [
    "from sqlalchemy.orm import Session",
    "from api.db import get_db",
    "from api.auth_scopes import verify_api_key_raw",
    "from fastapi import Depends",
]
for line in need:
    if line not in s:
        # insert after fastapi import line if possible, else prepend
        m = re.search(r"^from fastapi[^\n]*\n", s, flags=re.MULTILINE)
        if m:
            insert_at = m.end()
            s = s[:insert_at] + line + "\n" + s[insert_at:]
        else:
            s = line + "\n" + s

# Replace verify_api_key definition block
pattern = r"def\s+verify_api_key\s*\(.*?\)\s*:\n(?:^[ \t].*\n)+?(?=^\S|\Z)"
replacement = r'''def verify_api_key(
    x_api_key: str | None = None,
    db: Session = Depends(get_db),
) -> None:
    """
    Accept either:
      - legacy env key (FG_API_KEY / whatever _get_expected_api_key reads)
      - DB-backed API key (ApiKey.key_hash == hash_api_key(raw))
    """
    expected = _get_expected_api_key()

    if x_api_key is None or not str(x_api_key).strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    raw = str(x_api_key).strip()

    # 1) env legacy path
    if expected and raw == expected:
        return

    # 2) DB-backed path
    try:
        verify_api_key_raw(raw_key=raw, db=db, required_scopes=None)
        return
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
'''.strip() + "\n\n"

s2, n = re.subn(pattern, replacement, s, flags=re.MULTILINE | re.DOTALL)
if n == 0:
    raise SystemExit("[FAIL] Could not locate verify_api_key() block in api/auth.py")

p.write_text(s2, encoding="utf-8")
print("[OK] Patched api/auth.py verify_api_key() to accept env OR DB key")
