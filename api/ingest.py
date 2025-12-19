# api/ingest.py
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Tuple

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes
from api.db import get_db
from api.db_models import DecisionRecord
from api.ratelimit import rate_limit_guard

log = logging.getLogger("frostgate.ingest")

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ----------------------------
# Models (match agent payload)
# ----------------------------

class TelemetryInput(BaseModel):
    tenant_id: str = Field(..., description="Tenant identifier")
    source: str = Field(..., description="Telemetry source identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")
    event_type: str = Field(..., description="Event type (e.g., auth.bruteforce)")
    event: Dict[str, Any] = Field(default_factory=dict, description="Event payload")


class MitigationAction(BaseModel):
    action: Literal[
        "block_ip",
        "step_up_auth",
        "require_captcha",
        "throttle",
        "quarantine",
        "notify",
        "none",
    ]
    target: str
    reason: str
    confidence: float = 0.5


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = Field(default_factory=list)
    anomaly_score: float = 0.0
    llm_note: str = ""
    tie_d: Dict[str, Any] = Field(default_factory=dict)


class DefendResponse(BaseModel):
    event_id: str
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: List[MitigationAction] = Field(default_factory=list)
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int = 0


# ----------------------------
# Helpers
# ----------------------------

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_event_age_ms(event_ts: datetime) -> int:
    now = datetime.now(timezone.utc)
    event_ts = _to_utc(event_ts)
    return int((now - event_ts).total_seconds() * 1000)


def _compute_clock_drift_ms(event_ts: datetime) -> int:
    age_ms = _compute_event_age_ms(event_ts)
    stale_ms = int(os.getenv("FG_CLOCK_STALE_MS", "300000"))  # 5 min default
    if abs(age_ms) > stale_ms:
        return 0
    return age_ms


def _flatten_values_for_scan(obj: Any) -> Iterable[str]:
    if obj is None:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_values_for_scan(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _flatten_values_for_scan(v)
    else:
        yield str(obj)


def _detect_ai_adversarial(event: dict[str, Any]) -> float:
    suspicious_markers = [
        "ignore previous",
        "prompt injection",
        "system prompt",
        "exfil",
        "base64",
        "rm -rf",
        "curl http",
        "powershell -enc",
        "sudo ",
        "sshpass",
        "shadow file",
        "token",
        "api key",
    ]
    score = 0.0
    for text in _flatten_values_for_scan(event):
        t = text.lower()
        for marker in suspicious_markers:
            if marker in t:
                score += 0.15
    return float(max(0.0, min(score, 1.0)))


def _estimate_anomaly_score(event_type: str, event: dict[str, Any]) -> float:
    score = 0.05
    attempts = int(event.get("attempts") or event.get("failed_auths") or 0)
    score += min(attempts * 0.05, 0.5)

    if event.get("new_device") is True:
        score += 0.15
    if event.get("impossible_travel") is True:
        score += 0.2

    rep = event.get("network_reputation")
    if isinstance(rep, (int, float)):
        score += max(0.0, (1.0 - min(float(rep), 1.0))) * 0.2

    if event_type.startswith("auth.") or event_type.startswith("iam."):
        score += 0.05

    return float(max(0.0, min(score, 1.0)))


def _evaluate_rules(
    event_type: str,
    event: dict[str, Any],
) -> Tuple[
    Literal["none", "low", "medium", "high"],
    List[str],
    List[MitigationAction],
]:
    rules: List[str] = []
    mitigations: List[MitigationAction] = []
    threat: Literal["none", "low", "medium", "high"] = "none"

    src_ip = event.get("ip") or event.get("src_ip")
    user = event.get("username") or event.get("user") or "user:unknown"
    attempts = int(event.get("attempts") or event.get("failed_auths") or 0)

    if event_type.startswith("auth") and attempts >= 5 and src_ip:
        threat = "high"
        rules.append("rule:auth_bruteforce")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip),
                reason=f"{attempts} auth failures detected",
                confidence=0.92,
            )
        )

    if event.get("impossible_travel") is True:
        if threat != "high":
            threat = "medium"
        rules.append("rule:impossible_travel")
        mitigations.append(
            MitigationAction(
                action="step_up_auth",
                target=str(user),
                reason="impossible travel detected",
                confidence=0.74,
            )
        )

    if event.get("honeypot_touch") is True:
        threat = "high"
        rules.append("rule:honeypot_access")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip or "network:unknown"),
                reason="honeypot resource accessed",
                confidence=0.9,
            )
        )

    if not rules:
        rules.append("rule:default_allow")
        threat = "low"

    return threat, rules, mitigations


def _json_dumps_compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)


# ----------------------------
# Route: POST /ingest
# ----------------------------

@router.post(
    "",
    response_model=DefendResponse,
    dependencies=[
        Depends(require_scopes("ingest:write")),
        Depends(rate_limit_guard),
    ],
    operation_id="ingest_event",
)
async def ingest(
    req: TelemetryInput,
    request: Request,
    db: Session = Depends(get_db),
) -> DefendResponse:
    """
    Agent-facing ingestion endpoint.
    Performs decisioning and persists into decisions table.
    """
    start = time.perf_counter()

    event_id = str(uuid.uuid4())
    event_type = (req.event_type or "").strip() or "unknown"
    event = req.event or {}

    rule_threat, rules_triggered, mitigations = _evaluate_rules(event_type, event)
    anomaly_score = _estimate_anomaly_score(event_type, event)
    ai_adversarial_score = _detect_ai_adversarial(event)

    threat_level: Literal["none", "low", "medium", "high"] = rule_threat
    if threat_level == "none":
        threat_level = "low"

    max_signal = max(anomaly_score, ai_adversarial_score)
    if max_signal >= 0.75:
        threat_level = "high"
    elif max_signal >= 0.40 and threat_level == "low":
        threat_level = "medium"

    src_ip = event.get("ip") or event.get("src_ip")
    user = event.get("username") or event.get("user")

    if ai_adversarial_score >= 0.45:
        rules_triggered.append("guardrail:ai_adversarial")
    if anomaly_score >= 0.40 and "anomaly:behavioral" not in rules_triggered:
        rules_triggered.append("anomaly:behavioral")

    if ai_adversarial_score >= 0.75 and src_ip:
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip),
                reason="AI-adversarial payload patterns detected",
                confidence=0.70,
            )
        )
    elif ai_adversarial_score >= 0.45 and user:
        mitigations.append(
            MitigationAction(
                action="require_captcha",
                target=str(user),
                reason="Potential prompt-injection / automation abuse detected",
                confidence=0.55,
            )
        )
    elif threat_level in ("medium", "high") and src_ip:
        mitigations.append(
            MitigationAction(
                action="throttle",
                target=str(src_ip),
                reason="Elevated threat: apply throttling to reduce blast radius",
                confidence=0.55,
            )
        )

    event_age_ms = _compute_event_age_ms(req.timestamp)
    drift_ms = _compute_clock_drift_ms(req.timestamp)
    latency_ms = int((time.perf_counter() - start) * 1000)

    explain = DecisionExplain(
        summary=f"Ingest decision for tenant={req.tenant_id}, source={req.source}",
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        llm_note=(
            f"rules={','.join(rules_triggered)}; "
            f"anomaly={anomaly_score:.2f}; "
            f"ai_adv={ai_adversarial_score:.2f}; "
            "mode=enforce"
        ),
        tie_d={
            "event_age_ms": event_age_ms,
            "clock_drift_ms_reported": drift_ms,
            "latency_ms": latency_ms,
        },
    )

    resp = DefendResponse(
        event_id=event_id,
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=ai_adversarial_score,
        pq_fallback=False,
        clock_drift_ms=drift_ms,
    )

    # Persist decision best-effort
    try:
        record = DecisionRecord.from_request_and_response(
            tenant_id=req.tenant_id,
            source=req.source,
            event_id=event_id,
            event_type=event_type,
            threat_level=threat_level,
            anomaly_score=anomaly_score,
            ai_adversarial_score=ai_adversarial_score,
            pq_fallback=False,
            rules_triggered=rules_triggered,
            explain_summary=explain.summary,
            latency_ms=latency_ms,
            request_obj=req.model_dump(),
            response_obj=resp.model_dump(),
        )
        db.add(record)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        log.exception("ingest persist failed")

    return resp
