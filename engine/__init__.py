# engine/__init__.py

from __future__ import annotations

from typing import List

from api.schemas import (
    TelemetryInput,
    DefendResponse,
    MitigationAction,
    Persona,
    ClassificationRing,
    DecisionExplain,
    TIEDEstimate,
)

from engine.rules import evaluate_rules
from engine.roe import apply_roe, Mitigation
from engine.tied import estimate_impact
from engine.history import record_decision, list_decisions

# Alias so older type hints using DecisionResponse still make sense
DecisionResponse = DefendResponse

__all__ = [
    "evaluate_rules",
    "evaluate_with_doctrine",
    "record_decision",
    "list_decisions",
]


def evaluate_with_doctrine(telemetry: TelemetryInput) -> DecisionResponse:
    """
    Wrap the base rule engine with doctrine:
      - use persona + classification ring
      - run TIED impact estimate
      - apply ROE caps/flags
      - return a DefendResponse with enriched explain + adjusted mitigations
    """
    # Existing rules-only decision
    base: DecisionResponse = evaluate_rules(telemetry)

    # Fallbacks so engine is predictable even if callers omit these
    persona: Persona = telemetry.persona or Persona.GUARDIAN
    classification: ClassificationRing = (
        telemetry.classification or ClassificationRing.SECRET
    )

    # TIED impact estimate (service/user impact, gating, etc.)
    tie_d: TIEDEstimate = estimate_impact(
        threat_level=base.threat_level,
        classification=classification,
        persona=persona,
    )

    # Convert existing mitigations (Pydantic) into ROE's dataclass form
    mitigations_for_roe: List[Mitigation] = [
        Mitigation(
            action=m.action,
            target=m.target,
            reason=m.reason,
            confidence=m.confidence,
        )
        for m in base.mitigations
    ]

    # Apply rules of engagement on top of the raw decision
    roe_result = apply_roe(
        mitigations=mitigations_for_roe,
        persona=persona,
        ring=classification,
        tie_d=tie_d,
    )

    # Copy explain block so we don't mutate `base` in-place
    if base.explain is not None and hasattr(base.explain, "model_copy"):
        explain: DecisionExplain = base.explain.model_copy()
    elif base.explain is not None:
        # Very defensive; pydantic v1 fallback if this ever regresses
        explain = DecisionExplain(
            summary=base.explain.summary,
            rules_triggered=list(base.explain.rules_triggered),
            anomaly_score=getattr(base.explain, "anomaly_score", None),
            llm_note=getattr(base.explain, "llm_note", None),
        )
    else:
        explain = DecisionExplain(
            summary="Doctrine applied",
            rules_triggered=[],
            anomaly_score=None,
            llm_note=None,
        )

    # Enrich explain with doctrine metadata
    explain.persona = persona
    explain.classification = classification
    explain.tie_d = tie_d
    explain.roe_applied = True
    explain.disruption_limited = roe_result.disruption_limited
    explain.ao_required = roe_result.ao_required

    # Convert ROE mitigations back into API schema shape
    mitigations_out: List[MitigationAction] = [
        MitigationAction(
            action=m.action,
            target=m.target,
            reason=m.reason,
            confidence=m.confidence,
        )
        for m in roe_result.mitigations
    ]

    # Return an updated DefendResponse; preserve all the other base fields
    return base.model_copy(
        update={
            "mitigations": mitigations_out,
            "explain": explain,
        }
    )
