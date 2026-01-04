from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth_scopes import verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord

router = APIRouter(
    prefix="/dev",
    tags=["dev"],
    dependencies=[Depends(verify_api_key)],
)

# -----------------------------
# Helpers
# -----------------------------

def _dev_enabled() -> bool:
    # Hard gate: never ship this enabled by accident
    return os.getenv("FG_DEV_EVENTS_ENABLED", "0").strip() == "1"


def _utcnow_naive() -> datetime:
    # System uses naive UTC in sqlite rows.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _mk_ip(i: int) -> str:
    return f"10.0.{i % 255}.{(i * 7) % 255}"


def _score_from_threat(threat_level: str) -> int:
    t = (threat_level or "none").lower()
    if t in ("critical", "high"):
        return 90
    if t == "medium":
        return 60
    if t == "low":
        return 30
    return 0


def _confidence_from_sev(severity: str) -> float:
    s = (severity or "info").lower()
    if s in ("critical", "high"):
        return 0.95
    if s == "medium":
        return 0.85
    return 0.78


def _default_rules(threat_level: str, action_taken: str) -> List[str]:
    t = (threat_level or "none").lower()
    a = (action_taken or "log_only").lower()
    rules = [
        "rule:block_badness" if t != "none" else "rule:default_allow",
        "rule:rate_limit" if a == "rate_limited" else "rule:baseline",
    ]
    return rules


def _make_record(
    *,
    now: datetime,
    kind: str,
    src: str,
    tenant_id: Optional[str],
    ip: str,
    severity: str,
    threat_level: str,
    action_taken: str,
    pq_fallback: bool,
    seed_tag: Optional[str] = None,
    stable_key: Optional[str] = None,
) -> Tuple[str, DecisionRecord]:
    """
    Returns (event_id, DecisionRecord).
    If stable_key is provided, event_id is deterministic across runs.
    """
    base_anom = 0.0 if threat_level == "none" else (0.9 if threat_level in ("critical", "high") else 0.6)
    base_adv = 0.0 if not pq_fallback else 0.75

    req = {
        "kind": kind,
        "tenant_id": tenant_id,
        "source": src,
        "ip": ip,
        "ua": "dev-emitter",
        "ts": now.isoformat() + "Z",
        "seed": seed_tag,
    }

    decision = "block" if action_taken in ("blocked", "quarantined") else "allow"
    score = _score_from_threat(threat_level)
    confidence = _confidence_from_sev(severity)

    resp = {
        "severity": severity,
        "threat_level": threat_level,
        "action_taken": action_taken,
        "confidence": confidence,
        "score": score,
        "title": f"{kind} from {src}",
        "summary": f"{severity}/{threat_level} {action_taken}".strip(),
        "decision": decision,
        "pq_fallback": pq_fallback,
    }

    rules = _default_rules(threat_level, action_taken)

    # deterministic event id when seeding
    if stable_key:
        event_id = _sha(f"seed|{stable_key}")
    else:
        event_id = _sha(f"{kind}|{src}|{tenant_id}|{severity}|{threat_level}|{action_taken}|{ip}|{now.isoformat()}")

    diff = None
    if threat_level != "none":
        diff = {
            "changes": ["threat_level", "decision"],
            "prev": {"threat_level": "none", "decision": "allow"},
            "curr": {"threat_level": threat_level, "decision": decision},
            "summary": f"{seed_tag or 'dev emit'}: {action_taken}",
        }

    rec = DecisionRecord(
        created_at=now,
        tenant_id=tenant_id,
        source=src,
        event_id=event_id,
        event_type=kind,
        threat_level=threat_level,
        anomaly_score=base_anom,
        ai_adversarial_score=base_adv,
        pq_fallback=pq_fallback,
        rules_triggered_json=rules,
        decision_diff_json=diff,
        request_json=req,
        response_json=resp,
    )
    return event_id, rec


# -----------------------------
# Deterministic seed endpoint
# -----------------------------

@router.post("/seed")
def dev_seed(
    tenant_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Create a deterministic set of dev events for tests/demos.
    Idempotent: safe to call repeatedly without growing DB.
    """
    if not _dev_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    now = _utcnow_naive()
    src = "dev_seed"

    # 1) Non-actionable: low/info + log_only
    seeds = [
        dict(
            stable_key="low_log_only",
            kind="info",
            severity="info",
            threat_level="low",
            action_taken="log_only",
            pq_fallback=False,
            ip=_mk_ip(1),
        ),
        # 2) Actionable: high + blocked
        dict(
            stable_key="high_blocked",
            kind="waf",
            severity="high",
            threat_level="high",
            action_taken="blocked",
            pq_fallback=False,
            ip=_mk_ip(2),
        ),
        # 3) Optional: medium + rate_limited (nice for demos)
        dict(
            stable_key="medium_rate_limited",
            kind="edge_gw",
            severity="medium",
            threat_level="medium",
            action_taken="rate_limited",
            pq_fallback=False,
            ip=_mk_ip(3),
        ),
    ]

    created: List[int] = []
    existed: List[str] = []

    for s in seeds:
        event_id, rec = _make_record(
            now=now,
            kind=s["kind"],
            src=src,
            tenant_id=tenant_id,
            ip=s["ip"],
            severity=s["severity"],
            threat_level=s["threat_level"],
            action_taken=s["action_taken"],
            pq_fallback=s["pq_fallback"],
            seed_tag="dev seed",
            stable_key=s["stable_key"],
        )

        already = db.query(DecisionRecord.id).filter(DecisionRecord.event_id == event_id).first()
        if already:
            existed.append(event_id)
            continue

        db.add(rec)
        db.flush()
        created.append(int(rec.id))

    db.commit()
    return {"ok": True, "created": len(created), "created_ids": created, "already_present": len(existed)}


# -----------------------------
# Existing emit endpoint (kept)
# -----------------------------

@router.post("/emit")
def dev_emit(
    count: int = Query(10, ge=1, le=500),
    kind: Literal["auth_attempt", "waf", "edge_gw", "collector", "info"] = "auth_attempt",
    severity: Literal["critical", "high", "medium", "low", "info"] = "info",
    threat_level: Literal["critical", "high", "medium", "low", "none"] = "none",
    action_taken: Literal["blocked", "rate_limited", "quarantined", "log_only"] = "log_only",
    tenant_id: Optional[str] = None,
    source: Optional[str] = None,
    pq_fallback: bool = False,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if not _dev_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    src = source or kind
    created_ids: List[int] = []
    now = _utcnow_naive()

    for i in range(count):
        ip = _mk_ip(i)
        _, rec = _make_record(
            now=now,
            kind=kind,
            src=src,
            tenant_id=tenant_id,
            ip=ip,
            severity=severity,
            threat_level=threat_level,
            action_taken=action_taken,
            pq_fallback=pq_fallback,
            seed_tag="dev emit",
            stable_key=None,
        )
        db.add(rec)
        db.flush()
        created_ids.append(int(rec.id))

    db.commit()
    return {"ok": True, "created": len(created_ids), "ids": created_ids[-10:]}
