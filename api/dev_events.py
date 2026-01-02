from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

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

def _utcnow_naive() -> datetime:
    # Your system uses naive UTC in sqlite rows.
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _mk_ip(i: int) -> str:
    return f"10.0.{i % 255}.{(i * 7) % 255}"

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
    # Hard gate: never ship this enabled by accident
    if os.getenv("FG_DEV_EVENTS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    src = source or kind

    created_ids: List[int] = []
    now = _utcnow_naive()

    # Simple scoring just so UI has something to show + filter
    base_anom = 0.0 if threat_level == "none" else (0.9 if threat_level in ("critical", "high") else 0.6)
    base_adv = 0.0 if not pq_fallback else 0.75

    for i in range(count):
        ip = _mk_ip(i)

        # request_json: what came in
        req = {
            "kind": kind,
            "tenant_id": tenant_id,
            "source": src,
            "ip": ip,
            "ua": "dev-emitter",
            "ts": now.isoformat() + "Z",
        }

        # response_json: what we "decided"
        resp = {
            "severity": severity,
            "threat_level": threat_level,
            "action_taken": action_taken,
            "confidence": 0.95 if severity in ("critical", "high") else 0.78,
            "score": 90 if threat_level in ("critical", "high") else (60 if threat_level in ("medium", "low") else 0),
            "title": f"{kind} from {src}",
            "summary": f"Dev emit {kind} ({severity}/{threat_level}) -> {action_taken}",
            "decision": "block" if action_taken in ("blocked", "quarantined") else "allow",
            "pq_fallback": pq_fallback,
        }

        rules = [
            "rule:block_badness" if threat_level != "none" else "rule:default_allow",
            "rule:rate_limit" if action_taken == "rate_limited" else "rule:baseline",
        ]

        # Stable event id per emitted record
        event_id = _sha(f"{kind}|{src}|{tenant_id}|{severity}|{threat_level}|{action_taken}|{ip}|{i}|{now.isoformat()}")

        rec = DecisionRecord(
            tenant_id=tenant_id,
            source=src,
            event_id=event_id,
            event_type=kind,
            threat_level=threat_level,
            anomaly_score=base_anom,
            ai_adversarial_score=base_adv,
            pq_fallback=pq_fallback,
            rules_triggered_json=rules,
            decision_diff_json={
                "changes": ["threat_level", "decision"],
                "prev": {"threat_level": "none", "decision": "allow"},
                "curr": {"threat_level": threat_level, "decision": resp["decision"]},
                "summary": "Synthetic dev diff",
            } if threat_level != "none" else None,
            request_json=req,
            response_json=resp,
        )

        db.add(rec)
        db.flush()
        created_ids.append(int(rec.id))

    db.commit()
    return {"ok": True, "created": len(created_ids), "ids": created_ids[-10:]}
