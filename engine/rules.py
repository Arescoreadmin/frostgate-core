from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

from api.schemas import TelemetryInput, MitigationAction


def _as_dict(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if hasattr(x, "model_dump"):
        try:
            return x.model_dump()  # pydantic v2
        except Exception:
            return {}
    if hasattr(x, "dict"):
        try:
            return x.dict()  # pydantic v1
        except Exception:
            return {}
    return {}


def _norm_str(v: Any, default: str = "unknown") -> str:
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _coerce_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip()
        if not s:
            return default
        return int(float(s))
    except Exception:
        return default


def _normalize_event_type(event_type: Any) -> str:
    s = _norm_str(event_type, "unknown").lower()
    if s in ("auth.brute_force", "auth.bruteforce", "ssh.bruteforce", "bruteforce", "brute_force"):
        return "auth.bruteforce"
    return s


def _extract_payload_and_meta(
    telemetry: Union[TelemetryInput, Dict[str, Any], Any]
) -> Tuple[Dict[str, Any], str, str, str]:
    payload: Dict[str, Any] = {}
    event_type: Any = "unknown"
    tenant_id: Any = "unknown"
    source: Any = "unknown"

    if isinstance(telemetry, dict):
        payload_raw = telemetry.get("payload") or telemetry.get("event") or {}
        payload = payload_raw if isinstance(payload_raw, dict) else _as_dict(payload_raw)
        event_type = telemetry.get("event_type") or telemetry.get("type") or "unknown"
        tenant_id = telemetry.get("tenant_id") or telemetry.get("tenant") or "unknown"
        source = telemetry.get("source") or "unknown"
    else:
        payload_raw = getattr(telemetry, "payload", None) or getattr(telemetry, "event", None) or {}
        payload = payload_raw if isinstance(payload_raw, dict) else _as_dict(payload_raw)
        event_type = getattr(telemetry, "event_type", None) or getattr(telemetry, "type", None) or "unknown"
        tenant_id = getattr(telemetry, "tenant_id", None) or getattr(telemetry, "tenant", None) or "unknown"
        source = getattr(telemetry, "source", None) or "unknown"

    # fallback: some send event_type inside payload
    et = _norm_str(event_type, "unknown").lower()
    if et in ("unknown", "none", ""):
        event_type = payload.get("event_type") or payload.get("type") or event_type

    return payload, _normalize_event_type(event_type), _norm_str(tenant_id), _norm_str(source)


def evaluate_rules(
    telemetry: TelemetryInput,
) -> Tuple[str, List[MitigationAction], List[str], float, float]:
    """
    MVP rules engine.

    Returns:
      threat_level: low | medium | high | critical
      mitigations: list[MitigationAction]
      rules_triggered: list[str]
      anomaly_score: float 0-1
      ai_adv_score: float 0-1
    """
    threat_level = "low"
    mitigations: List[MitigationAction] = []
    rules_triggered: List[str] = []
    anomaly_score = 0.0
    ai_adv_score = 0.0

    payload, event_type, _tenant_id, _source = _extract_payload_and_meta(telemetry)

    source_ip = (
        payload.get("src_ip")
        or payload.get("source_ip")
        or payload.get("ip")
        or payload.get("client_ip")
        or "unknown"
    )

    failed_auths = _coerce_int(
        payload.get("failed_auths")
        or payload.get("failed_attempts")
        or payload.get("attempts")
        or payload.get("count")
        or payload.get("failures")
        or payload.get("num_failures")
        or payload.get("failed_logins")
        or 0
    )

    et = (event_type or "").lower()

    # robust bruteforce detection:
    # - explicit event_type indicates bruteforce
    # - OR high failed_auths implies bruteforce even if event_type is generic ("auth")
    is_bruteforce = ("bruteforce" in et) or ("brute_force" in et) or (failed_auths >= 10)

    # malformed telemetry (declares bruteforce but no count)
    if ("bruteforce" in et or "brute_force" in et) and failed_auths == 0:
        rules_triggered.append("rule:missing_failed_count")
        threat_level = "medium"
        anomaly_score = max(anomaly_score, 0.4)

    # Rule: brute-force auth
    if is_bruteforce and failed_auths >= 10:
        rules_triggered.append("rule:ssh_bruteforce")
        threat_level = "high"
        mitigations.append(
            MitigationAction(
                action="block",          # schema-valid
                target=str(source_ip),   # keep it string
                reason=f"{failed_auths} failed auth attempts detected",
                confidence=0.92,
            )
        )
        anomaly_score = max(anomaly_score, 0.8)

    # Rule: suspicious LLM usage marker
    if event_type == "suspicious_llm_usage":
        rules_triggered.append("rule:ai-assisted-attack")
        ai_adv_score = max(ai_adv_score, 0.7)
        if threat_level == "low":
            threat_level = "medium"

    return threat_level, mitigations, rules_triggered, anomaly_score, ai_adv_score
