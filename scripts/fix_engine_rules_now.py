#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re

p = Path("engine/rules.py")
s = p.read_text(encoding="utf-8")

# 1) Replace ANY failed_auths assignment block (even if broken) with a clean known-good one.
# We match from the line that starts with "failed_auths" through the next blank line.
pat = re.compile(r"(?ms)^\s*failed_auths\s*=.*?\n\s*\n")
replacement = """    failed_auths = int(
        payload.get("failed_auths")
        or payload.get("failed_attempts")
        or payload.get("attempts")
        or payload.get("count")
        or payload.get("failures")
        or payload.get("num_failures")
        or payload.get("failed_logins")
        or 0
    )

    is_bruteforce = "bruteforce" in str(event_type or "").lower()

"""

if not pat.search(s):
    raise SystemExit("[FAIL] Could not find failed_auths block to replace")

s = pat.sub(replacement, s, count=1)

# 2) Fix bruteforce rule conditions to use is_bruteforce with correct precedence.
s = re.sub(
    r'(?m)^\s*if\s+.*failed_auths\s*==\s*0\s*:\s*$',
    "    if is_bruteforce and failed_auths == 0:",
    s,
    count=1
)
s = re.sub(
    r'(?m)^\s*if\s+.*failed_auths\s*>=\s*10\s*:\s*$',
    "    if is_bruteforce and failed_auths >= 10:",
    s,
    count=1
)

p.write_text(s, encoding="utf-8")
print("[OK] engine/rules.py repaired (failed_auths + is_bruteforce + fixed conditions)")
