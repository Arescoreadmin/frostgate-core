from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes, verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord
from api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record
from api.ratelimit import rate_limit_guard
from api.schemas import TelemetryInput
from api.schemas_doctrine import TieD

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

# =============================================================================
# Time helpers
# =============================================================================


def _parse_dt(s: str) -> datetime:
    v = (s or "").strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def _to_utc(dt: datetime | str | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if isinstance(dt, str):
        dt = _parse_dt(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# =============================================================================
# Serialization helpers
# =============================================================================


def _safe_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        _safe_dump(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


# =============================================================================
# SQLAlchemy model/column helpers
# =============================================================================


def _filter_model_kwargs(model_cls: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only kwargs that match real columns on the model."""
    try:
        from sqlalchemy import inspect  # type: ignore

        cols = {a.key for a in inspect(model_cls).mapper.column_attrs}
        return {k: v for k, v in kwargs.items() if k in cols}
    except Exception:
        return kwargs


def _column_type_name(model_cls: Any, col_name: str) -> Optional[str]:
    """Return SQLAlchemy column type class name if possible (e.g., 'JSON', 'Text')."""
    try:
        from sqlalchemy import inspect  # type: ignore

        mapper = inspect(model_cls).mapper
        col = mapper.columns.get(col_name)
        if col is None:
            return None
        return col.type.__class__.__name__
    except Exception:
        return None


def _value_for_column(model_cls: Any, col_name: str, value: Any) -> Any:
    """
    If column is JSON-ish, pass Python objects.
    If column is TEXT-ish (SQLite common), serialize dict/list to JSON string.
    """
    tname = (_column_type_name(model_cls, col_name) or "").lower()

    if value is None:
        return None

    is_json_col = "json" in tname  # JSON / JSONB / etc
    if is_json_col:
        return value

    if isinstance(value, (dict, list, tuple)):
        return _canonical_json(value)

    return value


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
        or payload.get("failed_attempts")
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
    ts_val = getattr(req, "timestamp", _utcnow())
    ts = _iso(_to_utc(ts_val))
    et = _coerce_event_type(req)
    body = _coerce_event_payload(req)

    raw = f"{req.tenant_id}|{req.source}|{ts}|{et}|{_canonical_json(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_age_ms(event_ts: datetime | str) -> int:
    now = _utcnow()
    return int((now - _to_utc(event_ts)).total_seconds() * 1000)


def _clock_drift_ms(event_ts: datetime | str) -> int:
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
    rules_triggered: list[str] = Field(default_factory=list)
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None

    # Tests require this to exist (not None).
    tie_d: TieD = Field(default_factory=TieD)

    score: int = 0

    roe_applied: bool = False
    disruption_limited: bool = False
    ao_required: bool = False
    persona: Optional[str] = None
    classification: Optional[str] = None


class DefendResponse(BaseModel):
    # Tests require this to be a string, not None.
    explanation_brief: str
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: list[MitigationAction] = Field(default_factory=list)
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

    # MVP rule: auth brute force => block_ip
    if et in ("auth", "auth.bruteforce", "auth_attempt") and failed_auths >= 5 and src_ip:
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
) -> tuple[list[MitigationAction], TieD]:
    """
    Contract:
      - tie_d must always exist
      - guardian + SECRET:
          - roe_applied=True
          - ao_required=True
          - cap block_ip mitigations to 1
          - gating_decision present: allow | require_approval | reject
    """
    persona_v = (persona or "").strip().lower() or None
    class_v = (classification or "").strip().upper() or None

    roe_applied = False
    disruption_limited = False
    ao_required = False

    out = list(mitigations)

    # Baseline impacts (always initialized, no UnboundLocalError nonsense)
    base_impact = 0.0
    base_user_impact = 0.0

    if any(m.action == "block_ip" for m in out):
        base_impact = 0.35
        base_user_impact = 0.20

    if persona_v == "guardian" and class_v == "SECRET":
        roe_applied = True
        ao_required = True

        # cap block_ip to 1 (guardian cap)
        block_ips = [m for m in out if m.action == "block_ip"]
        if len(block_ips) > 1:
            disruption_limited = True
            first = block_ips[0]
            out = [m for m in out if m.action != "block_ip"]
            out.insert(0, first)

        # doctrine reduces blast radius by limiting actions
        if disruption_limited:
            base_impact = max(0.0, base_impact - 0.10)
            base_user_impact = max(0.0, base_user_impact - 0.05)

    # gating decision: allow | require_approval | reject
    gating_decision: Literal["allow", "require_approval", "reject"] = "allow"
    if persona_v == "guardian" and class_v == "SECRET":
        # require approval if we actually took a disruptive action
        gating_decision = "require_approval" if any(m.action == "block_ip" for m in out) else "allow"

    tied = TieD(
        roe_applied=roe_applied,
        disruption_limited=disruption_limited,
        ao_required=ao_required,
        persona=persona_v,
        classification=class_v,
        service_impact=float(min(1.0, max(0.0, base_impact))),
        user_impact=float(min(1.0, max(0.0, base_user_impact))),
        gating_decision=gating_decision,
        # policy_version is defaulted in TieD, but leaving explicit is fine if you prefer:
        # policy_version="doctrine-v1",
    )

    return out, tied


# =============================================================================
# Tamper-evident chain hash (best effort)
# =============================================================================


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _compute_chain_hash(prev_hash: Optional[str], payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(f"{prev_hash or ''}:{blob}")


def _supports_chain_fields() -> bool:
    return hasattr(DecisionRecord, "prev_hash") and hasattr(DecisionRecord, "chain_hash")


def _hash_payload(
    *,
    event_id: str,
    created_at: datetime,
    tenant_id: str,
    source: str,
    event_type: str,
    threat_level: str,
    rules_triggered: list[str],
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "created_at": _iso(created_at),
        "tenant_id": tenant_id,
        "source": source,
        "event_type": event_type,
        "threat_level": threat_level,
        "rules_triggered": rules_triggered,
    }


# =============================================================================
# Persistence (best effort)
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
    score: int,
) -> None:
    ts_val = getattr(req, "timestamp", _utcnow())
    created_at = _to_utc(ts_val)

    request_payload = {
        "tenant_id": req.tenant_id,
        "source": req.source,
        "timestamp": _iso(created_at),
        "event_type": event_type,
        "event": _coerce_event_payload(req),
        "persona": getattr(req, "persona", None),
        "classification": getattr(req, "classification", None),
    }
    response_payload = _safe_dump(decision)

    try:
        record_kwargs: dict[str, Any] = {
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
        }

        rules_value = list(rules_triggered or [])
        req_value = dict(request_payload)
        resp_value = response_payload

        # --- Decision Diff (compute + persist) ---
        decision_diff_obj = None
        try:
            prev = (
                db.query(DecisionRecord)
                .filter(
                    DecisionRecord.tenant_id == req.tenant_id,
                    DecisionRecord.source == req.source,
                    DecisionRecord.event_type == event_type,
                )
                .order_by(DecisionRecord.id.desc())
                .first()
            )
            prev_snapshot = snapshot_from_record(prev) if prev is not None else None
            curr_snapshot = snapshot_from_current(
                threat_level=str(decision.threat_level),
                rules_triggered=rules_value,
                score=int(score or 0),
            )
            decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)

            if hasattr(DecisionRecord, "decision_diff_json"):
                record_kwargs["decision_diff_json"] = decision_diff_obj
        except Exception:
            log.exception("decision diff compute/persist failed")
            decision_diff_obj = None
        # --- end Decision Diff ---

        # rules_triggered_json / request_json / response_json
        for col, val in (
            ("rules_triggered_json", rules_value),
            ("request_json", req_value),
            ("response_json", resp_value),
            ("request_obj", req_value),
            ("response_obj", resp_value),
        ):
            if hasattr(DecisionRecord, col):
                record_kwargs[col] = _value_for_column(DecisionRecord, col, val)

        record = DecisionRecord(**_filter_model_kwargs(DecisionRecord, record_kwargs))

        if _supports_chain_fields():
            last = db.query(DecisionRecord).order_by(DecisionRecord.id.desc()).first()
            prev_hash = getattr(last, "chain_hash", None) if last else None
            record.prev_hash = prev_hash
            record.chain_hash = _compute_chain_hash(
                prev_hash,
                _hash_payload(
                    event_id=event_id,
                    created_at=created_at,
                    tenant_id=req.tenant_id,
                    source=req.source,
                    event_type=event_type,
                    threat_level=str(decision.threat_level),
                    rules_triggered=rules_value,
                ),
            )

        db.add(record)
        db.commit()
    except IntegrityError:
        db.rollback()
        # event_id may be unique; treat duplicates as OK
        return
    except Exception:
        db.rollback()
        log.exception("failed to persist decision")


# =============================================================================
# Endpoint
# =============================================================================


@router.post("", response_model=DefendResponse)
def defend(req: TelemetryInput, db: Session = Depends(get_db)) -> DefendResponse:
    t0 = time.time()

    event_type = _coerce_event_type(req)
    event_id = _event_id(req)

    ts_val = getattr(req, "timestamp", _utcnow())
    clock_drift = _clock_drift_ms(ts_val)

    threat_level, rules_triggered, mitigations, anomaly_score, score = evaluate(req)

    persona = getattr(req, "persona", None)
    classification = getattr(req, "classification", None)

    mitigations, tie_d = _apply_doctrine(persona, classification, mitigations)

    summary = f"{event_type}: {threat_level} ({score})"

    explain = DecisionExplain(
        summary=summary,
        rules_triggered=list(rules_triggered),
        anomaly_score=float(anomaly_score or 0.0),
        score=int(score or 0),
        tie_d=tie_d,
        roe_applied=bool(tie_d.roe_applied),
        disruption_limited=bool(tie_d.disruption_limited),
        ao_required=bool(tie_d.ao_required),
        persona=tie_d.persona,
        classification=tie_d.classification,
    )

    resp = DefendResponse(
        explanation_brief=summary,  # must be str for tests
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=0.0,
        pq_fallback=False,
        clock_drift_ms=int(clock_drift or 0),
        event_id=event_id,
    )

    latency_ms = int((time.time() - t0) * 1000)
    _persist_decision_best_effort(
        db=db,
        req=req,
        event_id=event_id,
        event_type=event_type,
        decision=resp,
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        latency_ms=latency_ms,
        score=score,
    )

    return resp
