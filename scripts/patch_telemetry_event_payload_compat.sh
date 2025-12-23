#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"

echo "[*] Backing up api/schemas.py..."
cp -a api/schemas.py "api/schemas.py.bak.${TS}"

echo "[*] Rewriting api/schemas.py with payload/event compatibility + backfills..."

cat > api/schemas.py <<'PY'
# api/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TelemetryInput(BaseModel):
    """
    Canonical request model for defend/ingest.

    Compatibility goals:
      - New shape: payload={...} (tests use this)
      - Legacy shape: event={...} (defend.py still references req.event)
      - Legacy root fields: event_type, src_ip (defend.py references req.event_type)
      - Doctrine: classification/persona must accept plain strings
      - Allow extra fields for forward compatibility
    """
    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None

    # Doctrine fields
    classification: Optional[str] = Field(default=None)
    persona: Optional[str] = Field(default=None)

    # Canonical event container
    payload: Dict[str, Any] = Field(default_factory=dict)

    # Legacy alias container
    event: Optional[Dict[str, Any]] = None

    # Legacy compat fields
    event_type: Optional[str] = Field(default=None)
    src_ip: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def _normalize(self) -> "TelemetryInput":
        # 1) If event exists and payload is empty, use event as payload
        if isinstance(self.event, dict) and self.event and (not isinstance(self.payload, dict) or not self.payload):
            self.payload = dict(self.event)

        # 2) If payload exists and event is missing, mirror payload into event
        if isinstance(self.payload, dict) and self.payload and (self.event is None):
            self.event = dict(self.payload)

        # 3) Backfill event_type and src_ip from payload/event
        src = self.payload if isinstance(self.payload, dict) else {}
        if not src and isinstance(self.event, dict):
            src = self.event

        if not self.event_type:
            et = src.get("event_type")
            if isinstance(et, str) and et.strip():
                self.event_type = et.strip()

        if not self.src_ip:
            ip = src.get("src_ip")
            if isinstance(ip, str) and ip.strip():
                self.src_ip = ip.strip()

        return self


TelemetryInput.model_rebuild()
PY

echo "[*] Quick compile..."
python -m py_compile api/schemas.py api/defend.py api/main.py

echo "[*] Run the doctrine test that was failing..."
pytest -q tests/test_doctrine.py::test_guardian_disruption_limit_and_roe_flags -q

echo "[✓] Patch applied."
