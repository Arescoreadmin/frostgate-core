from __future__ import annotations

"""
Compatibility shim.

Rules engine currently returns a legacy tuple:
    (threat_level, mitigations, rules_triggered, anomaly_score, ai_adv_score)

For /ingest (telemetry ingest), we want a normalized JSON-safe decision dict.
"""

from typing import Any, Dict, List

try:
    from engine import evaluate_rules as _evaluate_rules  # type: ignore
except Exception:
    from engine.rules import evaluate_rules as _evaluate_rules  # type: ignore


def _to_jsonable_mitigations(mits: Any) -> List[Dict[str, Any]]:
    if not mits:
        return []
    out: List[Dict[str, Any]] = []
    for m in mits:
        # Pydantic v2
        if hasattr(m, "model_dump"):
            out.append(m.model_dump())
        # Pydantic v1
        elif hasattr(m, "dict"):
            out.append(m.dict())
        # plain dict already
        elif isinstance(m, dict):
            out.append(m)
        else:
            # last resort: string it
            out.append({"raw": str(m)})
    return out


def evaluate(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate telemetry envelope and return normalized decision dict.

    telemetry is expected to include:
      tenant_id, source, event_type, payload
    """
    result = _evaluate_rules(telemetry)

    # If someone later upgrades rules engine to return dict, support that.
    if isinstance(result, dict):
        # make sure mitigations are JSON-safe
        result = dict(result)
        result["mitigations"] = _to_jsonable_mitigations(result.get("mitigations"))
        # normalize key naming
        if "rules_triggered" in result and "rules" not in result:
            result["rules"] = result.pop("rules_triggered")
        return result

    # Legacy tuple normalization
    try:
        threat_level, mitigations, rules_triggered, anomaly_score, ai_adv_score = result
    except Exception:
        return {
            "tenant_id": telemetry.get("tenant_id", "unknown"),
            "source": telemetry.get("source", "unknown"),
            "event_type": telemetry.get("event_type", "unknown"),
            "threat_level": "low",
            "mitigations": [],
            "rules": [],
            "anomaly_score": 0.0,
            "ai_adversarial_score": 0.0,
            "error": f"Unexpected evaluate_rules return: {type(result)}",
        }

    return {
        "tenant_id": telemetry.get("tenant_id", "unknown"),
        "source": telemetry.get("source", "unknown"),
        "event_type": telemetry.get("event_type", "unknown"),
        "threat_level": threat_level,
        "mitigations": _to_jsonable_mitigations(mitigations),
        "rules": list(rules_triggered or []),
        "anomaly_score": float(anomaly_score or 0.0),
        "ai_adversarial_score": float(ai_adv_score or 0.0),
    }
