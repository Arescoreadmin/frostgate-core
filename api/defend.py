from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes, verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord
from api.ratelimit import rate_limit_guard
from api.schemas import TelemetryInput

log = logging.getLogger("frostgate.defend")

ERR_INVALID = "Invalid or missing API key"

router = APIRouter(
    prefix="/defend",
    tags=["defend"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_scopes("defend:write")),
        Depends(rate_limit_guard),
    ],
)

# =============================================================================
# Time helpers (ONE source of truth)
# =============================================================================


def _parse_dt(s: str) -> datetime:
    v = (s or "").strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def _to_utc(dt: datetime | str) -> datetime:
    """Accept datetime OR ISO-8601 string and normalize to tz-aware UTC."""
    if isinstance(dt, str):
        dt = _parse_dt(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# =============================================================================
# Serialization helpers
# =============================================================================


def _safe_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _canonical_json(obj: Any) -> str:
    return json.dumps(_safe_dump(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# =============================================================================
# DB helper (column-safe kwargs)
# =============================================================================


def _filter_model_kwargs(model_cls: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to only mapped columns to avoid schema drift explosions."""
    try:
        from sqlalchemy import inspect  # type: ignore

        cols = {a.key for a in inspect(model_cls).mapper.column_attrs}
        return {k: v for k, v in kwargs.items() if k in cols}
    except Exception:
        return kwargs


# =============================================================================
# Normalization helpers
# =============================================================================


def _coerce_event_type(req: TelemetryInput) -> str:
    et = getattr(req, "event_type", None)
    payload = getattr(req, "payload", None)
    event = getattr(req, "event", None)

    if not et and isinstance(payload, dict):
        et = payload.get("event_type")
    if not et and isinstance(event, dict):
        et = event.get("event_type")

    et = (et or "").strip()
    return et or "unknown"


def _coerce_event_payload(req: TelemetryInput) -> dict[str, Any]:
    event = getattr(req, "event", None)
    payload = getattr(req, "payload", None)

    if isinstance(event, dict) and event:
        return dict(event)
    if isinstance(payload, dict) and payload:
        return dict(payload)
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


# =============================================================================
# Event identity + timing
# =============================================================================


def _event_id(req: TelemetryInput) -> str:
    ts_val = getattr(req, "timestamp", datetime.now(timezone.utc))
    ts = _to_utc(ts_val).isoformat().replace("+00:00", "Z")
    et = _coerce_event_type(req)
    body = _coerce_event_payload(req)

    raw = f"{req.tenant_id}|{req.source}|{ts}|{et}|{_canonical_json(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_age_ms(event_ts: datetime | str) -> int:
    now = datetime.now(timezone.utc)
    return int((now - _to_utc(event_ts)).total_seconds() * 1000)


def _clock_drift_ms(event_ts: datetime | str) -> int:
    """
    Contract invariant: never negative.
    If event time is wildly stale beyond FG_CLOCK_STALE_MS, report 0 (ignore drift).
    """
    age_ms = _event_age_ms(event_ts)
    stale_ms = int(os.getenv("FG_CLOCK_STALE_MS", "300000"))  # 5 min
    return 0 if abs(age_ms) > stale_ms else abs(age_ms)


# =============================================================================
# Models
# =============================================================================


class MitigationAction(BaseModel):
    action: str
    target: Optional[str] = None
    reason: str
    confidence: float = 1.0
    meta: Optional[dict[str, Any]] = None


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: list[str] = []
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None
    tie_d: Optional[dict[str, Any]] = None
    score: int = 0

    # Doctrine flags
    roe_applied: bool = False
    disruption_limited: bool = False
    ao_required: bool = False
    persona: Optional[str] = None
    classification: Optional[str] = None


class DefendResponse(BaseModel):
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: list[MitigationAction] = []
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int
    event_id: str


# =============================================================================
# Scoring (MVP rules engine)
# =============================================================================

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


def evaluate(
    req: TelemetryInput,
) -> Tuple[Literal["none", "low", "medium", "high"], list[str], list[MitigationAction], float, int]:
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


# =============================================================================
# Doctrine (minimal, contract-friendly)
# =============================================================================


def _apply_doctrine(
    persona: Optional[str],
    classification: Optional[str],
    mitigations: list[MitigationAction],
) -> tuple[list[MitigationAction], dict[str, Any]]:
    persona_v = (persona or "").strip().lower() or None
    class_v = (classification or "").strip().upper() or None

    roe_applied = False
    disruption_limited = False
    ao_required = False

    out = list(mitigations)

    # Contract: guardian + SECRET => roe_applied True; ao_required present; tie_d keys exist
    if persona_v == "guardian" and class_v == "SECRET":
        roe_applied = True
        ao_required = True

        block_ips = [m for m in out if m.action == "block_ip"]
        if len(block_ips) > 1:
            disruption_limited = True
            first = block_ips[0]
            out = [m for m in out if m.action != "block_ip"]
            out.insert(0, first)

    return out, {
        "roe_applied": roe_applied,
        "disruption_limited": disruption_limited,
        "ao_required": ao_required,
        "persona": persona_v,
        "classification": class_v,
    }


# =============================================================================
# Tamper-evident chain hash (best effort)
# =============================================================================


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _compute_chain_hash(prev_hash: Optional[str], payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(f"{prev_hash or ''}:{blob}")


def _supports_chain_fields() -> bool:
    # Avoid hard dependency on schema. If columns don’t exist, skip silently.
    return hasattr(DecisionRecord, "prev_hash") and hasattr(DecisionRecord, "chain_hash")


def _canonical_hash_payload(
    *,
    event_id: str,
    created_at: datetime,
    tenant_id: str,
    source: str,
    event_type: str,
    severity: str,
    rules_triggered: list[str],
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "source": source,
        "event_type": event_type,
        "severity": severity,
        "rules_triggered": rules_triggered,
    }


# =============================================================================
# Persistence (best effort, never break the endpoint)
# =============================================================================


def _persist_decision_best_effort(
    *,
    db: Session,
    req: TelemetryInput,
    event_id: str,
    event_type: str,
    decision: DefendResponse,
    rules_triggered: list[str],
    anomaly_score: float,
    latency_ms: int,
) -> None:
    ts_val = getattr(req, "timestamp", datetime.now(timezone.utc))
    created_at = _to_utc(ts_val)

    request_obj = {
        "tenant_id": req.tenant_id,
        "source": req.source,
        "timestamp": created_at.isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "event": _coerce_event_payload(req),
        "persona": getattr(req, "persona", None),
        "classification": getattr(req, "classification", None),
    }
    response_obj = decision.model_dump(mode="json") if hasattr(decision, "model_dump") else decision.dict()

    try:
        # Prefer factory if present
        if hasattr(DecisionRecord, "from_request_and_response"):
            record = DecisionRecord.from_request_and_response(
                tenant_id=req.tenant_id,
                source=req.source,
                event_id=event_id,
                event_type=event_type,
                threat_level=decision.threat_level,
                anomaly_score=float(anomaly_score or 0.0),
                ai_adversarial_score=float(decision.ai_adversarial_score or 0.0),
                pq_fallback=bool(decision.pq_fallback),
                rules_triggered=rules_triggered,
                explain_summary=decision.explain.summary,
                latency_ms=int(latency_ms or 0),
                request_obj=request_obj,
                response_obj=response_obj,
            )
        else:
            record_kwargs = {
                "tenant_id": req.tenant_id,
                "source": req.source,
                "event_id": event_id,
                "event_type": event_type,
                "threat_level": decision.threat_level,
                "anomaly_score": float(anomaly_score or 0.0),
                "ai_adversarial_score": float(decision.ai_adversarial_score or 0.0),
                "pq_fallback": bool(decision.pq_fallback),
                "explain_summary": decision.explain.summary,
                "latency_ms": int(latency_ms or 0),
                "request_obj": request_obj,
                "response_obj": response_obj,
                "rules_triggered": rules_triggered,
                "created_at": created_at,
            }
            record = DecisionRecord(**_filter_model_kwargs(DecisionRecord, record_kwargs))

        # Chain hash: compute prev from DB last record (best effort)
        if _supports_chain_fields():
            last = db.query(DecisionRecord).order_by(DecisionRecord.id.desc()).first()
            prev = getattr(last, "chain_hash", None) if last else None

            hp = _canonical_hash_payload(
                event_id=event_id,
                created_at=created_at,
                tenant_id=req.tenant_id,
                source=req.source,
                event_type=event_type,
                severity=str(decision.threat_level),
                rules_triggered=rules_triggered,
            )
            record.prev_hash = prev
            record.chain_hash = _compute_chain_hash(prev, hp)

        db.add(record)
        db.flush()
        db.commit()

        log.info(
            "persisted decision tenant=%s event_id=%s event_type=%s threat=%s",
            req.tenant_id,
            event_id,
            event_type,
            decision.threat_level,
        )

    except IntegrityError:
        db.rollback()
        log.info("duplicate decision ignored tenant=%s event_id=%s", req.tenant_id, event_id)

    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        log.exception(
            "FAILED to persist decision tenant=%s event_id=%s event_type=%s",
            req.tenant_id,
            event_id,
            event_type,
        )

        if os.getenv("FG_DEBUG_DECISIONS", "false").lower() in ("1", "true", "yes", "on"):
            log.error("DEBUG_DECISION request=%s", _canonical_json(req))
            log.error("DEBUG_DECISION response=%s", _canonical_json(decision))


# =============================================================================
# Route
# =============================================================================


@router.post("", response_model=DefendResponse)
async def defend(request: TelemetryInput, db: Session = Depends(get_db)) -> DefendResponse:
    start = time.perf_counter()

    event_type = _coerce_event_type(request)
    event_id = _event_id(request)

    threat_level, rules_triggered, mitigations, anomaly_score, score = evaluate(request)

    mitigations, doctrine = _apply_doctrine(
        persona=getattr(request, "persona", None),
        classification=getattr(request, "classification", None),
        mitigations=mitigations,
    )

    ts_val = getattr(request, "timestamp", datetime.now(timezone.utc))
    drift_ms = _clock_drift_ms(ts_val)
    latency_ms = int((time.perf_counter() - start) * 1000)

    tie_d = {
        "event_age_ms": _event_age_ms(ts_val),
        "clock_drift_ms_reported": drift_ms,
        "latency_ms": latency_ms,
        "service_impact": float(0.10 if doctrine["disruption_limited"] else 0.05),
        "user_impact": float(0.20 if doctrine["ao_required"] else 0.05),
        "gating_decision": ("require_approval" if doctrine["ao_required"] else "allow"),
    }

    decision = DefendResponse(
        threat_level=threat_level,
        mitigations=mitigations,
        explain=DecisionExplain(
            summary=f"MVP decision for tenant={request.tenant_id}, source={request.source}",
            rules_triggered=rules_triggered,
            anomaly_score=float(anomaly_score or 0.0),
            llm_note="Rules+score engine. Deterministic.",
            tie_d=tie_d,
            score=int(score or 0),
            roe_applied=bool(doctrine["roe_applied"]),
            disruption_limited=bool(doctrine["disruption_limited"]),
            ao_required=bool(doctrine["ao_required"]),
            persona=doctrine["persona"],
            classification=doctrine["classification"],
        ),
        ai_adversarial_score=0.0,
        pq_fallback=False,
        clock_drift_ms=int(drift_ms or 0),
        event_id=event_id,
    )

    _persist_decision_best_effort(
        db=db,
        req=request,
        event_id=event_id,
        event_type=event_type,
        decision=decision,
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        latency_ms=latency_ms,
    )

    return decision
