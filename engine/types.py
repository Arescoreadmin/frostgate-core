from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from api.schemas import TelemetryInput



class ThreatLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClassificationRing(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SECRET = "SECRET"
    TOP_SECRET = "TOP_SECRET"


class Persona(str, Enum):
    GUARDIAN = "guardian"
    SENTINEL = "sentinel"


class TieDBlock(BaseModel):
    service_impact: float
    user_impact: float
    gating_decision: str


class ExplainBlock(BaseModel):
    summary: str
    rules_triggered: List[str]
    anomaly_score: float
    llm_note: Optional[str] = None

     # Doctrine / TIED metadata that doctrine + tests expect
    classification: Optional[str] = None        # or Optional[ClassificationRing]
    persona: Optional[str] = None
    roe_applied: Optional[bool] = None
    disruption_limited: Optional[bool] = None
    ao_required: Optional[bool] = None


class DecisionExplain(ExplainBlock):
    classification: Optional[ClassificationRing] = None
    persona: Optional[Persona] = None
    tie_d: Optional[TieDBlock] = None
    roe_applied: bool = False
    disruption_limited: bool = False
    ao_required: bool = False


class MitigationAction(BaseModel):
    action: str
    target: Optional[str] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None
    meta: Optional[Dict[str, Any]] = None


class legacyTelemetryInput(BaseModel):
    source: str
    tenant_id: str
    timestamp: str  # keep as string; api.main parses it manually
    payload: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None
    classification: Optional[ClassificationRing] = None
    persona: Optional[Persona] = None


class DefendResponse(BaseModel):
    threat_level: str  # evaluate_rules returns simple strings
    mitigations: List[MitigationAction] = Field(default_factory=list)
    explain: DecisionExplain | ExplainBlock
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int = 0


__all__ = [
    "ThreatLevel",
    "ClassificationRing",
    "Persona",
    "TieDBlock",
    "ExplainBlock",
    "DecisionExplain",
    "MitigationAction",
    "TelemetryInput",
    "DefendResponse",
]
