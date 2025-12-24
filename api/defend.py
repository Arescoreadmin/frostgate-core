from __future__ import annotations

def _to_utc(dt):
    """
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' and naive datetimes.
    """
    from datetime import datetime, timezone

    if dt is None:
        return datetime.now(timezone.utc)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, Optional

from api.auth_scopes import require_scopes, verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord
from api.ratelimit import rate_limit_guard

from api.schemas import TelemetryInput


log = logging.getLogger("frostgate.defend")

router = APIRouter(
    prefix="/defend",
    tags=["defend"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_scopes("defend:write")),
        Depends(rate_limit_guard),
    ],
)

# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------



class DefendRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str
    tenant_id: str
    timestamp: str
    classification: Optional[str] = None
    persona: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class LegacyTelemetryInput(BaseModel):
    """
    Backward compatible:
      - Old clients: {tenant_id, source, timestamp, payload:{...}}
      - New clients: {tenant_id, source, timestamp, event_type:"...", event:{...}}
      - Also supports event_type inside payload as payload.event_type
    """
    source: str = Field(..., description="Telemetry source identifier")
    tenant_id: str = Field(..., description="Tenant identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC, ISO8601)")

    # Old path
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw telemetry payload")

    # Newer shape (optional)
    event_type: Optional[str] = Field(default=None, description="Top-level event type")
    event: Optional[dict[str, Any]] = Field(default=None, description="Top-level event body")


class MitigationAction(BaseModel):
    action: str
    target: Optional[str] = None
    reason: str
    confidence: float = 1.0
    meta: Optional[dict[str, Any]] = None


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = []
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None
    tie_d: Optional[dict[str, Any]] = None
    score: int = 0


class DefendResponse(BaseModel):
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: List[MitigationAction] = []
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int
    event_id: str


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _to_utc(dt: datetime | str | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


def _safe_dump(obj: Any) -> Any:
    # Pydantic v2-safe: datetime -> ISO string, etc.
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _canonical_json(obj: Any) -> str:
    return json.dumps(_safe_dump(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _coerce_event_type(req: TelemetryInput) -> str:
    """
    Never lose event_type again. Priority:
      1) top-level req.event_type
      2) req.payload.event_type
      3) req.event.event_type
      4) unknown
    """
    et = req.event_type
    if not et and isinstance(req.payload, dict):
        et = req.payload.get("event_type")
    if not et and isinstance(req.event, dict):
        et = req.event.get("event_type")

    et = (et or "").strip()
    return et or "unknown"


def _coerce_event_payload(req: TelemetryInput) -> dict[str, Any]:
    """
    Normalize data for scoring logic:
      - If top-level `event` exists, that's the event body.
      - Else, use `payload` as the event body.
    """
    if isinstance(req.event, dict) and req.event:
        return dict(req.event)
    if isinstance(req.payload, dict) and req.payload:
        return dict(req.payload)
    return {}


def _normalize_ip(payload: dict[str, Any]) -> Optional[str]:
    v = (
        payload.get("src_ip")
        or payload.get("source_ip")
        or payload.get("source_ip_addr")
        or payload.get("ip")
        or payload.get("remote_ip")
    )
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_failed_auths(payload: dict[str, Any]) -> int:
    raw = (
        payload.get("failed_auths")
        or payload.get("fail_count")
        or payload.get("failures")
        or payload.get("attempts")
        or 0
    )
    try:
        return int(raw)
    except Exception:
        return 0


def _event_id(req: TelemetryInput) -> str:
    """
    Deterministic ID based on tenant/source/timestamp + normalized event_type + normalized payload.
    Important: include event_type, otherwise different event_types could collide.
    """
    ts = _to_utc(req.timestamp).isoformat().replace("+00:00", "Z")
    et = _coerce_event_type(req)
    body = _coerce_event_payload(req)

    raw = f"{req.tenant_id}|{req.source}|{ts}|{et}|{_canonical_json(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_age_ms(event_ts: datetime) -> int:
    now = datetime.now(timezone.utc)
    return int((now - _to_utc(event_ts)).total_seconds() * 1000)


def _clock_drift_ms(event_ts: datetime) -> int:
    age_ms = _event_age_ms(event_ts)
    stale_ms = int(os.getenv("FG_CLOCK_STALE_MS", "300000"))  # 5 minutes
    return 0 if abs(age_ms) > stale_ms else age_ms


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------

RULE_SCORES: dict[str, int] = {
    "rule:ssh_bruteforce": 90,
    "rule:default_allow": 0,
}


def _threat_from_score(score: int) -> Literal["none", "low", "medium", "high"]:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    if score >= 20:
        return "low"
    return "none"


def evaluate(req: TelemetryInput) -> Tuple[
    Literal["none", "low", "medium", "high"],
    list[str],
    list[MitigationAction],
    float,
    int,
]:
    et = _coerce_event_type(req)
    body = _coerce_event_payload(req)

    failed_auths = _normalize_failed_auths(body)
    src_ip = _normalize_ip(body)

    rules_triggered: list[str] = []
    mitigations: list[MitigationAction] = []
    anomaly_score = 0.1

    if et in ("auth", "auth.bruteforce") and failed_auths >= 5 and src_ip:
        rules_triggered.append("rule:ssh_bruteforce")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=src_ip,
                reason=f"{failed_auths} failed auth attempts detected",
                confidence=0.92,
            )
        )
        anomaly_score = 0.8
    else:
        rules_triggered.append("rule:default_allow")

    score = sum(RULE_SCORES.get(r, 0) for r in rules_triggered)
    threat_level = _threat_from_score(score)
    return threat_level, rules_triggered, mitigations, anomaly_score, score


# ---------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------


@router.post("", response_model=DefendResponse)
async def defend(request: TelemetryInput, db: Session = Depends(get_db)) -> DefendResponse:
    start = time.perf_counter()

    # Normalize early so logs + DB + response all match
    safe_event_type = _coerce_event_type(request)
    safe_event_body = _coerce_event_payload(request)

    eid = _event_id(request)
    threat_level, rules_triggered, mitigations, anomaly_score, score = evaluate(request)

    drift_ms = _clock_drift_ms(request.timestamp)
    latency_ms = int((time.perf_counter() - start) * 1000)

    decision = DefendResponse(
        threat_level=threat_level,
        mitigations=mitigations,
        explain=DecisionExplain(
            summary=f"MVP decision for tenant={request.tenant_id}, source={request.source}",
            rules_triggered=rules_triggered,
            anomaly_score=anomaly_score,
            llm_note="Rules+score engine. Deterministic.",
            tie_d={
                "event_age_ms": _event_age_ms(request.timestamp),
                "clock_drift_ms_reported": drift_ms,
                "latency_ms": latency_ms,
            },
            score=score,
        ),
        ai_adversarial_score=0.0,
        pq_fallback=False,
        clock_drift_ms=drift_ms,
        event_id=eid,
    )

    debug = os.getenv("FG_DEBUG_DECISIONS", "false").lower() in ("1", "true", "yes", "on")

    # Persist (best effort). Never crash the endpoint for DB issues in MVP mode.
    try:
        record = DecisionRecord.from_request_and_response(
            tenant_id=request.tenant_id,
            source=request.source,
            event_id=eid,
            event_type=safe_event_type,
            threat_level=decision.threat_level,
            anomaly_score=float(decision.explain.anomaly_score or 0.0),
            ai_adversarial_score=float(decision.ai_adversarial_score or 0.0),
            pq_fallback=bool(decision.pq_fallback),
            rules_triggered=decision.explain.rules_triggered,
            explain_summary=decision.explain.summary,
            latency_ms=int(latency_ms or 0),
            request_obj={
                "tenant_id": request.tenant_id,
                "source": request.source,
                "timestamp": _to_utc(request.timestamp).isoformat().replace("+00:00", "Z"),
                "event_type": safe_event_type,
                "event": safe_event_body,
            },
            response_obj=decision.model_dump(mode="json"),
        )
        db.add(record)
        db.flush()
        db.commit()

        log.info(
            "persisted decision tenant=%s event_id=%s event_type=%s threat=%s score=%s",
            request.tenant_id,
            eid,
            safe_event_type,
            decision.threat_level,
            score,
        )

    except IntegrityError:
        db.rollback()
        log.info("duplicate decision ignored tenant=%s event_id=%s", request.tenant_id, eid)

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass

        log.exception(
            "FAILED to persist decision tenant=%s event_id=%s event_type=%s",
            request.tenant_id,
            eid,
            safe_event_type,
        )

        if debug:
            log.error("DEBUG_DECISION request=%s", _canonical_json(request))
            log.error("DEBUG_DECISION response=%s", _canonical_json(decision))
            log.error("DEBUG_DECISION exception=%r", e)

    return decision

# Patched: ensure doctrine always sees a boolean
try:
    explain["disruption_limited"] = bool(explain.get("disruption_limited", False))
except Exception:
    pass
