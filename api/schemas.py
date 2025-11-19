from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --- Core enums -------------------------------------------------------------

class Persona(str, Enum):
    GUARDIAN = "guardian"
    SENTINEL = "sentinel"


class ClassificationRing(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    SECRET = "SECRET"


# --- Doctrine / TIED --------------------------------------------------------

class TIEDEstimate(BaseModel):
    """
    TIED: Tenant / Infrastructure / End-user / Data impact estimate.
    Values are 0.0–1.0, higher = more impact / risk.
    """
    service_impact: float = Field(..., ge=0.0, le=1.0)
    user_impact: float = Field(..., ge=0.0, le=1.0)
    # "allow" | "require_approval" | "reject" (stringly-typed is fine for now)
    gating_decision: str


class DecisionExplain(BaseModel):
    """
    Human + machine-readable explanation object for a defense decision.
    Extends the earlier MVP explain block with doctrine fields.
    """

    # MVP fields (used in existing tests & responses)
    summary: str
    rules_triggered: list[str]
    anomaly_score: float | None = None
    llm_note: str | None = None

    # doctrine additions
    classification: ClassificationRing | None = None
    persona: Persona | None = None
    tie_d: TIEDEstimate | None = None

    # ROE output flags
    roe_applied: bool | None = None
    disruption_limited: bool | None = None
    ao_required: bool | None = None

# Backwards-compat alias used by api.main & tests
ExplainBlock = DecisionExplain


# --- Mitigations & defend response -----------------------------------------

class MitigationAction(BaseModel):
    action: str          # e.g. "block_ip", "require_captcha"
    target: str          # e.g. "192.0.2.10", "user:alice"
    reason: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    meta: Optional[Dict[str, Any]] = None


class DefendResponse(BaseModel):
    threat_level: str                    # "low" | "medium" | "high"
    mitigations: List[MitigationAction] = Field(default_factory=list)
    explain: ExplainBlock

    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: Optional[int] = None


# --- Ingress telemetry ------------------------------------------------------

from datetime import datetime
from typing import Dict, Any, Optional

class TelemetryInput(BaseModel):
    """
    Ingress payload for /defend and related endpoints.

    Tests & jobs construct JSON with:
      - source
      - tenant_id
      - timestamp (ISO 8601 string)
      - payload: free-form dict (e.g., failed_auths, ip, user, etc.)
    """

    source: str
    tenant_id: str
    timestamp: datetime
    payload: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None

    # v1 / doctrine fields (optional, backwards compatible)
    classification: Optional[ClassificationRing] = None
    persona: Optional[Persona] = None

    model_config = {
        "extra": "ignore",  # keep ignoring unknowns for safety
    }


__all__ = [
    "Persona",
    "ClassificationRing",
    "TIEDEstimate",
    "DecisionExplain",
    "ExplainBlock",
    "MitigationAction",
    "DefendResponse",
    "TelemetryInput",
]
