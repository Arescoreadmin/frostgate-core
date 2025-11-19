from __future__ import annotations

from typing import Final

from api.schemas import TIEDEstimate, ClassificationRing, Persona

# Base impact scores by threat level
_BASE_SERVICE_IMPACT: Final[dict[str, float]] = {
    "low": 0.2,
    "medium": 0.5,
    "high": 0.8,
}

_BASE_USER_IMPACT: Final[dict[str, float]] = {
    "low": 0.1,
    "medium": 0.4,
    "high": 0.7,
}

# Classification amplifiers, keyed by *string* form, to avoid enum name assumptions
_RING_MULTIPLIER: Final[dict[str, float]] = {
    "unclass": 0.8,
    "unclassified": 0.8,
    "cui": 1.0,
    "secret": 1.2,
}

# Persona adjustment factors
_GUARDIAN_BOOST: Final[float] = 1.1
_SENTINEL_DAMPEN: Final[float] = 0.9


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def estimate_impact(
    *,
    threat_level: str,
    classification: ClassificationRing,
    persona: Persona,
) -> TIEDEstimate:
    """
    Heuristic TIED estimator.

    Inputs:
      - threat_level: 'low' | 'medium' | 'high' (or unknown, treated as medium-ish)
      - classification: UNCLASS / CUI / SECRET (matched by string, case-insensitive)
      - persona:
          GUARDIAN => slightly pessimistic (higher impact)
          SENTINEL => slightly optimistic (lower impact)

    Outputs:
      - service_impact: 0..1
      - user_impact:    0..1
      - gating_decision: 'allow' | 'require_approval' | 'reject'
    """

    # Normalize threat level and get base scores with sane defaults
    norm_level = (threat_level or "").lower()
    base_service = _BASE_SERVICE_IMPACT.get(norm_level, 0.3)
    base_user = _BASE_USER_IMPACT.get(norm_level, 0.2)

    # Normalize classification via value or str(), then lower
    cls_str = getattr(classification, "value", str(classification))
    cls_key = str(cls_str).lower()
    ring_multiplier = _RING_MULTIPLIER.get(cls_key, 1.0)

    service_impact = _clamp(base_service * ring_multiplier)
    user_impact = _clamp(base_user * ring_multiplier)

    # persona: guardian slightly pessimistic, sentinel slightly optimistic
    if persona == Persona.GUARDIAN:
        service_impact = _clamp(service_impact * _GUARDIAN_BOOST)
        user_impact = _clamp(user_impact * _GUARDIAN_BOOST)
    else:
        # treat everything not-guardian as "more willing to take risk"
        service_impact = _clamp(service_impact * _SENTINEL_DAMPEN)
        user_impact = _clamp(user_impact * _SENTINEL_DAMPEN)

    # gating decision aligned with tests:
    #   "allow", "require_approval", "reject"
    if service_impact < 0.4 and user_impact < 0.4:
        gate = "allow"
    elif service_impact < 0.7 and user_impact < 0.7:
        gate = "require_approval"
    else:
        gate = "reject"

    return TIEDEstimate(
        service_impact=service_impact,
        user_impact=user_impact,
        gating_decision=gate,
        notes=(
            "heuristic TIED estimate: "
            f"threat_level={norm_level or threat_level!r}, "
            f"classification={cls_str}, persona={getattr(persona, 'value', str(persona))}"
        ),
    )
