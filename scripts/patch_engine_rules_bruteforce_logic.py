#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re

p = Path("engine/rules.py")
s = p.read_text(encoding="utf-8")

# Insert a stable is_bruteforce flag after failed_auths block (first occurrence)
if "is_bruteforce =" not in s:
    s = re.sub(
        r"(?ms)(^\s*failed_auths\s*=\s*int\(\s*.*?\)\s*\n)",
        r"\1\n    is_bruteforce = 'bruteforce' in str(event_type or '').lower()\n",
        s,
        count=1
    )

# Replace the two bruteforce if-lines with correct parenthesized logic
s = re.sub(
    r"(?m)^\s*if\s+event_type\s*==\s*\"auth\.bruteforce\".*failed_auths\s*==\s*0\s*:\s*$",
    "    if is_bruteforce and failed_auths == 0:",
    s
)
s = re.sub(
    r"(?m)^\s*if\s+event_type\s*==\s*\"auth\.bruteforce\".*failed_auths\s*>=\s*10\s*:\s*$",
    "    if is_bruteforce and failed_auths >= 10:",
    s
)

p.write_text(s, encoding="utf-8")
print("[OK] Patched engine/rules.py bruteforce logic (is_bruteforce flag + fixed conditions)")
