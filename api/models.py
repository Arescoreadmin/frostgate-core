from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from api.schemas import TelemetryInput

from sqlalchemy import Column, DateTime, text

created_at = Column(
    DateTime,
    nullable=False,
    server_default=text("CURRENT_TIMESTAMP"),
)


class LegacyTelemetryInput(BaseModel):
    """
    Minimal MVP telemetry envelope accepted by /defend.
    """

    source: str = Field(
        ..., description="Telemetry source identifier (e.g., edge gateway id)"
    )
    tenant_id: str = Field(..., description="Tenant identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw telemetry payload, schema varies by event_type",
    )


class DefendRequest(TelemetryInput):
    """
    Backwards-compat for older code that expected DefendRequest as the input type.
    For now it's identical to TelemetryInput.
    """

    pass


class MitigationAction(BaseModel):
    action: str
    target: Optional[str] = None
    reason: str
    confidence: float = 1.0
    meta: Optional[dict[str, Any]] = None


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = []
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None

    # placeholders for future doctrine / ROE integration
    classification: Optional[str] = None
    persona: Optional[str] = None
    tie_d: Optional[dict[str, Any]] = None
    roe_applied: Optional[dict[str, Any]] = None
    disruption_limited: Optional[bool] = None
    ao_required: Optional[bool] = None


class DefendResponse(BaseModel):
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: List[MitigationAction] = []
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int


__all__ = [
    "TelemetryInput",
    "DefendRequest",
    "MitigationAction",
    "DecisionExplain",
    "DefendResponse",
]
