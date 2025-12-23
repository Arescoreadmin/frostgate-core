#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

p = Path("engine/rules.py")
s = p.read_text(encoding="utf-8")

# Replace the failed_auths int(...) block with a broader one.
pattern = r"failed_auths\s*=\s*int\(\s*payload\.get\(\"failed_auths\"\).*?\)\s*\n"
replacement = r'''failed_auths = int(
        payload.get("failed_auths")
        or payload.get("failed_attempts")
        or payload.get("attempts")
        or payload.get("count")
        or payload.get("failures")
        or payload.get("num_failures")
        or payload.get("failed_logins")
        or 0
    )
'''
s2, n = re.subn(pattern, replacement, s, flags=re.DOTALL | re.MULTILINE)
if n == 0:
    print("[WARN] Could not replace failed_auths block (pattern not found). Check engine/rules.py manually.")
else:
    p.write_text(s2, encoding="utf-8")
    print("[OK] Patched engine/rules.py to recognize more bruteforce count keys")
