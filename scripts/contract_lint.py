#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

CONTRACT_PATH = Path("CONTRACT.md")

REQUIRED_TOP_HEADERS = [
    "0) Principles",
    "1) Configuration and Environment Precedence",
    "2) Auth, Scopes, Rate Limiting",
    "3) `/defend` Endpoint Contract",
    "4) Telemetry Input Normalization",
    "5) Decision Engine MVP Rules",
    "6) Doctrine and ROE Persona Gate",
    "7) Clock Drift",
    "8) Persistence (Best Effort, Defined)",
    "9) Tamper-Evident Logging (Current State)",
    "10) `/feed/live` Contract",
    "11) Dev Seed Contract (`FG_DEV_EVENTS_ENABLED`)",
    "12) Non-Goals (Explicit)",
    "13) Change Control",
]

# Minimum strings that must appear somewhere in the contract.
# This is intentionally opinionated: it prevents “oh we forgot to paste the rules” regressions.
REQUIRED_PHRASES = [
    "build_app(auth_enabled",
    "FG_AUTH_ENABLED",
    "FG_API_KEY",
    "X-API-Key",
    "Invalid or missing API key",
    "POST /defend",
    "event_id",
    "clock_drift_ms",
    "only_actionable=true",
    "action_taken",
    "severity",
    "FG_DEV_EVENTS_ENABLED=1",
    "POST /dev/seed",
    "source == \"dev_seed\"",
]

HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _die(msg: str) -> int:
    print(f"❌ CONTRACT LINT FAILED: {msg}", file=sys.stderr)
    return 1


def _warn(msg: str) -> None:
    print(f"⚠️  {msg}", file=sys.stderr)


def _extract_headers(md: str) -> List[str]:
    return [m.group(1).strip() for m in HEADER_RE.finditer(md)]


def _find_duplicates(items: List[str]) -> Dict[str, int]:
    seen: Dict[str, int] = {}
    dup: Dict[str, int] = {}
    for x in items:
        if x in seen:
            dup[x] = dup.get(x, 1) + 1
        else:
            seen[x] = 1
    return dup


def main() -> int:
    if not CONTRACT_PATH.exists():
        return _die("CONTRACT.md not found at repo root.")

    md = CONTRACT_PATH.read_text(encoding="utf-8")

    # 1) Required major headers exist
    for h in REQUIRED_TOP_HEADERS:
        if h not in md:
            return _die(f"Missing required section header: `{h}`")

    # 2) Duplicate headers check (usually pasted blocks or drift)
    headers = _extract_headers(md)
    dups = _find_duplicates(headers)
    if dups:
        pretty = ", ".join([f"`{k}`(x{v})" for k, v in sorted(dups.items())])
        return _die(f"Duplicate `##` headers detected: {pretty}")

    # 3) Required phrases exist (simple but effective)
    missing = [p for p in REQUIRED_PHRASES if p not in md]
    if missing:
        return _die("Missing required contract phrases: " + ", ".join([f"`{m}`" for m in missing]))

    # 4) Basic sanity: enforce a Table of Contents exists
    if "Table of Contents" not in md:
        _warn("No 'Table of Contents' found. Not failing, but you should keep it.")

    print("✅ CONTRACT LINT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
