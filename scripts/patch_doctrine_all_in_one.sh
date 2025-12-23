#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
echo "[*] Patch timestamp: ${TS}"

backup() {
  local f="$1"
  if [[ -f "$f" ]]; then
    cp -a "$f" "${f}.bak.${TS}"
    echo "    [+] backed up $f -> ${f}.bak.${TS}"
  fi
}

backup api/schemas.py
backup api/defend.py

echo "[*] Rewriting api/schemas.py with TelemetryInput + MitigationAction (engine compat)..."
cat > api/schemas.py <<'PY'
# api/schemas.py
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MitigationAction(str, Enum):
    # Keep these stable: tests + engine import these.
    observe = "observe"
    alert = "alert"
    rate_limit = "rate_limit"
    challenge = "challenge"
    block_ip = "block_ip"


class TelemetryInput(BaseModel):
    """
    Canonical request model for defend/ingest.

    Compatibility:
      - New shape: payload={...} (tests use this)
      - Legacy shape: event={...} (defend.py references req.event in places)
      - Legacy root fields: event_type/src_ip (defend.py references req.event_type)
      - Doctrine: classification/persona as plain strings (tests pass "SECRET", "guardian")
      - extra=allow so we don't brick forward fields during MVP
    """
    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None

    classification: Optional[str] = None
    persona: Optional[str] = None

    # these may exist at root OR inside payload/event
    event_type: Optional[str] = None
    src_ip: Optional[str] = None

    payload: Dict[str, Any] = Field(default_factory=dict)
    event: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        payload = data.get("payload")
        event = data.get("event")

        # If only one exists, mirror it into the other for legacy code paths.
        if isinstance(payload, dict) and not isinstance(event, dict):
            data["event"] = payload
        elif isinstance(event, dict) and not isinstance(payload, dict):
            data["payload"] = event
        elif not isinstance(payload, dict) and not isinstance(event, dict):
            data["payload"] = {}
            data["event"] = {}

        # Backfill event_type + src_ip from payload/event if missing at root.
        p = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        e = data.get("event") if isinstance(data.get("event"), dict) else {}

        if data.get("event_type") is None:
            data["event_type"] = p.get("event_type") or e.get("event_type")
        if data.get("src_ip") is None:
            data["src_ip"] = p.get("src_ip") or e.get("src_ip")

        return data
PY

echo "[*] Patching api/defend.py to parse timestamp strings safely..."
python - <<'PY'
from __future__ import annotations
from pathlib import Path
import re

p = Path("api/defend.py")
src = p.read_text(encoding="utf-8")

# 1) Ensure we have a robust _to_utc that accepts datetime|str|None.
to_utc_block = """def _to_utc(dt):
    \"\"\"Normalize datetime OR ISO-8601 string to tz-aware UTC datetime.\"\"\"
    from datetime import datetime, timezone

    if dt is None:
        return datetime.now(timezone.utc)

    # datetime -> normalize
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # string -> parse
    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)
"""

if "def _to_utc" in src:
    # Replace existing def _to_utc(...) block (best-effort).
    pattern = r"^def _to_utc\([^\)]*\):\n(?:^[ \t].*\n)*"
    m = re.search(pattern, src, flags=re.M)
    if m:
        src = src[:m.start()] + to_utc_block + "\n\n" + src[m.end():]
    else:
        # Found mention but not matchable block, just inject a new one near top.
        insert_at = src.find("\n\n")
        src = src[:insert_at] + "\n\n" + to_utc_block + "\n\n" + src[insert_at:]
else:
    # Inject it after imports (roughly).
    # Put it after the last import block.
    lines = src.splitlines(True)
    idx = 0
    for i, line in enumerate(lines):
        if line.startswith("from ") or line.startswith("import ") or line.strip() == "":
            idx = i
        else:
            break
    src = "".join(lines[:idx+1]) + "\n" + to_utc_block + "\n" + "".join(lines[idx+1:])

# 2) Make sure any helper expecting datetime uses _to_utc() on req.timestamp.
# Your earlier crash was: _to_utc(req.timestamp).tzinfo access, where _to_utc assumed datetime.
# We don't need to rewrite call sites now, because _to_utc handles str.

p.write_text(src, encoding="utf-8")
print("[+] patched api/defend.py (_to_utc tolerant)")
PY

echo "[*] Quick compile..."
python -m py_compile api/schemas.py api/defend.py api/main.py api/auth_scopes.py api/feed.py

echo "[*] Run the previously failing tests (fast subset)..."
pytest -q tests/test_engine_rules.py -q
pytest -q tests/test_doctrine.py::test_guardian_disruption_limit_and_roe_flags -q

echo "[✓] Patch complete."
