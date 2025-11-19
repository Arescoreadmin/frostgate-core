# engine/__init__.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from api.schemas import (
    TelemetryInput,
    DefendResponse,
    ExplainBlock,        # alias for DecisionExplain
    MitigationAction,
    Persona,
    ClassificationRing,
)

from .rules import evaluate_rules
from .roe import apply_roe, Mitigation
from .persona import get_persona_profile
from .tied import estimate_impact

__all__ = [
    "evaluate_rules",
    "record_decision",
    "list_decisions",
    "evaluate_with_doctrine",
]

# --------------------------------------------------------------------------------------
# In-memory decision history
# --------------------------------------------------------------------------------------

_DECISION_HISTORY: List[Dict[str, Any]] = []
_MAX_HISTORY = 1000  # cheap cap so this never explodes in a long-running process


def record_decision(
    *,
    tenant_id: str,
    source: str,
    threat_level: str,
    rules_triggered: List[str],
    anomaly_score: float,
    ai_adv_score: float,
    pq_fallback: bool,
    clock_drift_ms: int,
) -> None:
    """
    Append a single decision into in-memory history.

    This is intentionally simple; it's an MVP operational/debug facility,
    NOT durable storage.
    """
    global _DECISION_HISTORY

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "source": source,
        "threat_level": threat_level,
        "rules_triggered": list(rules_triggered),
        "anomaly_score": anomaly_score,
        "ai_adversarial_score": ai_adv_score,
        "pq_fallback": pq_fallback,
        "clock_drift_ms": clock_drift_ms,
    }

    _DECISION_HISTORY.append(entry)

    # enforce simple cap (drop oldest)
    if len(_DECISION_HISTORY) > _MAX_HISTORY:
        _DECISION_HISTORY = _DECISION_HISTORY[-_MAX_HISTORY:]


def list_decisions(
    *,
    tenant_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Return recent decisions, optionally filtered by tenant_id.
    """
    items = _DECISION_HISTORY
    if tenant_id is not None:
        items = [d for d in items if d.get("tenant_id") == tenant_id]

    # most recent first
    items = list(reversed(items))
    return items[:limit]


# --------------------------------------------------------------------------------------
# Doctrine / ROE / TIED overlay
# --------------------------------------------------------------------------------------


def evaluate_with_doctrine(
    *,
    telemetry: TelemetryInput,
    base_threat_level: str,
    base_mitigations: List[MitigationAction],
    base_explain: ExplainBlock,
    base_ai_adv_score: float,
    pq_fallback: bool,
    clock_drift_ms: int,
) -> DefendResponse:
    """
    Overlay doctrine / ROE / TIED on top of the base rules decision.

    Inputs:
      - telemetry: raw TelemetryInput (may or may not include persona/classification)
      - base_*: output from evaluate_rules

    Output:
      - DefendResponse with enriched explain & ROE-filtered mitigations
    """
    persona = getattr(telemetry, "persona", None) or Persona.GUARDIAN
    classification = getattr(telemetry, "classification", None) or ClassificationRing.CUI

    # persona profile currently unused for math, but reserved for tuning
    _profile = get_persona_profile(persona)

    tie_d = estimate_impact(
        threat_level=base_threat_level,
        classification=classification,
        persona=persona,
    )

    mitigations = [
        Mitigation(
            action=m.action,
            target=m.target,
            reason=m.reason,
            confidence=m.confidence,
        )
        for m in base_mitigations
    ]

    roe_result = apply_roe(
        mitigations=mitigations,
        persona=persona,
        ring=classification,
        tie_d=tie_d,
    )

    # mutate base_explain in-place
    explain = base_explain
    explain.persona = persona
    explain.classification = classification
    explain.tie_d = tie_d
    explain.roe_applied = True
    explain.disruption_limited = roe_result.disruption_limited
    explain.ao_required = roe_result.ao_required

    # Pydantic v2 is picky: give DefendResponse a dict, not a model instance
    explain_dict = (
        explain.model_dump()
        if hasattr(explain, "model_dump")
        else dict(explain)
    )

    return DefendResponse(
        threat_level=base_threat_level,
        mitigations=[
            MitigationAction(
                action=m.action,
                target=m.target,
                reason=m.reason,
                confidence=m.confidence,
            )
            for m in roe_result.mitigations
        ],
        explain=explain_dict,
        ai_adversarial_score=base_ai_adv_score,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )
