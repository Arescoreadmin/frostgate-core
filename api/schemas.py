# api/schemas.py
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MitigationAction(BaseModel):
    """
    Engine expects MitigationAction(...) as a structured object (keyword args).
    Keep permissive for MVP: action is a string.
    """

    model_config = ConfigDict(extra="allow")

    action: str
    target: Optional[str] = None
    reason: Optional[str] = None
    confidence: float = 0.5


class TelemetryInput(BaseModel):
    """
    Canonical request model for defend/ingest.

    Compatibility:
      - New shape: payload={...} (tests use this)
      - Legacy shape: event={...} (defend.py references req.event)
      - Root fields: event_type/src_ip (defend.py references req.event_type)
      - Doctrine: classification/persona as plain strings
      - extra=allow for forward compatibility during MVP
    """

    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None

    # Doctrine fields as strings
    classification: Optional[str] = None
    persona: Optional[str] = None

    # New + legacy containers
    payload: Dict[str, Any] = Field(default_factory=dict)
    event: Dict[str, Any] = Field(default_factory=dict)

    # Backfilled convenience fields (defend.py references these directly)
    event_type: Optional[str] = None
    src_ip: Optional[str] = None

    @model_validator(mode="after")
    def _compat_backfill(self) -> "TelemetryInput":
        # If one of payload/event missing, mirror the other
        if not isinstance(self.payload, dict):
            self.payload = {}
        if not isinstance(self.event, dict):
            self.event = {}

        if not self.payload and self.event:
            self.payload = dict(self.event)
        if not self.event and self.payload:
            self.event = dict(self.payload)

        # Backfill event_type/src_ip from containers if missing
        if not self.event_type:
            self.event_type = (
                self.payload.get("event_type") or self.event.get("event_type") or None
            )
        if not self.src_ip:
            self.src_ip = (
                self.payload.get("src_ip")
                or self.event.get("src_ip")
                or self.payload.get("source_ip")
                or self.event.get("source_ip")
                or None
            )

        return self
