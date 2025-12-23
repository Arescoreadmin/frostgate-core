#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"

echo "[*] Backing up api/schemas.py..."
cp -a api/schemas.py "api/schemas.py.bak.${TS}"

echo "[*] Patching api/schemas.py to backfill event_type/src_ip from payload..."

cat > api/schemas.py <<'PY'
# api/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TelemetryInput(BaseModel):
    """
    Canonical request model for ingest/defend.

    Key requirements:
      - Accept doctrine fields (classification/persona) as strings.
      - Accept event_type coming either:
          a) legacy root field (event_type)
          b) modern payload["event_type"] (tests use this)
      - Allow extra fields for forward compatibility.
    """
    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None

    # Doctrine fields
    classification: Optional[str] = Field(default=None)
    persona: Optional[str] = Field(default=None)

    # Legacy/compat fields (defend.py currently accesses req.event_type sometimes)
    event_type: Optional[str] = Field(default=None)
    src_ip: Optional[str] = Field(default=None)

    # Event payload (tests send event_type inside this)
    payload: Dict[str, Any]

    @model_validator(mode="after")
    def _backfill_compat_fields(self) -> "TelemetryInput":
        # Backfill event_type and src_ip from payload for compatibility
        try:
            if isinstance(self.payload, dict):
                if not self.event_type:
                    et = self.payload.get("event_type")
                    if isinstance(et, str) and et.strip():
                        self.event_type = et.strip()

                if not self.src_ip:
                    ip = self.payload.get("src_ip")
                    if isinstance(ip, str) and ip.strip():
                        self.src_ip = ip.strip()
        except Exception:
            # Don't ever fail schema validation because of this
            pass
        return self


TelemetryInput.model_rebuild()
PY

echo "[*] Quick compile..."
python -m py_compile api/schemas.py api/defend.py api/main.py

echo "[*] Re-running doctrine failing test..."
pytest -q tests/test_doctrine.py::test_guardian_disruption_limit_and_roe_flags -q

echo "[✓] Telemetry backfill patch applied."
