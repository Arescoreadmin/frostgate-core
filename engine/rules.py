from typing import List, Tuple

from api.schemas import TelemetryInput, MitigationAction


def evaluate_rules(
    telemetry: TelemetryInput,
) -> Tuple[str, List[MitigationAction], List[str], float, float]:
    """
    MVP rules engine.

    Returns:
        threat_level: low | medium | high | critical
        mitigations: list of MitigationAction
        rules_triggered: list of rule ids
        anomaly_score: float 0-1
        ai_adv_score: float 0-1
    """
    payload = telemetry.payload
    source_ip = payload.get("src_ip") or payload.get("source_ip", "unknown")
    event_type = payload.get("event_type", "unknown")
    failed_auths = int(payload.get("failed_auths", 0))

    rules_triggered: List[str] = []
    mitigations: List[MitigationAction] = []
    threat_level = "low"
    anomaly_score = 0.1
    ai_adv_score = 0.0

    # Rule 1: brute-force auth
    if failed_auths >= 10:
        rules_triggered.append("rule:ssh_bruteforce")
        threat_level = "high"
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=source_ip,
                reason=f"{failed_auths} failed auth attempts detected",
                confidence=0.92,
            )
        )
        anomaly_score = 0.8

    # Rule 2: suspicious LLM usage marker
    if event_type == "suspicious_llm_usage":
        rules_triggered.append("rule:ai-assisted-attack")
        ai_adv_score = 0.7
        if threat_level == "low":
            threat_level = "medium"

    return threat_level, mitigations, rules_triggered, anomaly_score, ai_adv_score
