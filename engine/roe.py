from dataclasses import dataclass
from typing import Iterable, List

from api.schemas import Persona, ClassificationRing, TIEDEstimate


@dataclass
class Mitigation:
    action: str
    target: str
    reason: str
    confidence: float


@dataclass
class ROEResult:
    mitigations: List[Mitigation]
    disruption_limited: bool
    ao_required: bool


# Ultra-minimal ROE config for MVP.
ROE_CONFIG = {
    "default": {
        "allowed_actions": ["block_ip", "flag_session"],
        "max_confidence_drop_guardian": 0.1,
        "max_disruptive_actions_guardian": 1,
        "max_disruptive_actions_sentinel": 3,
        "ao_required_for_secret_high": True,
    }
}


def apply_roe(
    mitigations: Iterable[Mitigation],
    persona: Persona,
    ring: ClassificationRing,
    tie_d: TIEDEstimate | None = None,
) -> ROEResult:
    cfg = ROE_CONFIG["default"]
    allowed = set(cfg["allowed_actions"])

    filtered: list[Mitigation] = []
    disruptive_count = 0
    disruption_limited = False
    ao_required = False

    for m in mitigations:
        if m.action not in allowed:
            # Hard deny by ROE
            continue

        # Basic persona-based disruption limits
        if persona == Persona.GUARDIAN:
            max_disruptive = cfg["max_disruptive_actions_guardian"]
        else:
            max_disruptive = cfg["max_disruptive_actions_sentinel"]

        if m.action == "block_ip":
            disruptive_count += 1
            if disruptive_count > max_disruptive:
                disruption_limited = True
                continue

        filtered.append(m)

    # Basic AO flag:
    # SECRET + high predicted impact + high threat => require AO
    if (
        ring == ClassificationRing.SECRET
        and tie_d is not None
        and tie_d.gating_decision == "reject"
    ):
        ao_required = True

    return ROEResult(
        mitigations=filtered,
        disruption_limited=disruption_limited,
        ao_required=ao_required,
    )
