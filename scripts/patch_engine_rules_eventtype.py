#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

p = Path("engine/rules.py")
s = p.read_text(encoding="utf-8")

# Broaden checks: auth.bruteforce OR auth.*bruteforce OR ssh.bruteforce etc
def broaden(block: str) -> str:
    return block.replace('event_type == "auth.bruteforce"', 'event_type == "auth.bruteforce" or "bruteforce" in event_type')

s2 = broaden(s)
if s2 != s:
    p.write_text(s2, encoding="utf-8")
    print("[OK] Broadened bruteforce event_type match to include any '*bruteforce*'")
else:
    print("[SKIP] No exact auth.bruteforce comparisons found to broaden")
