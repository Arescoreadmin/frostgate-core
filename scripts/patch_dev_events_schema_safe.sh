#!/usr/bin/env bash
set -euo pipefail

FILE="api/dev_events.py"
ts="$(date +%Y%m%d_%H%M%S)"

if [[ -f "$FILE" ]]; then
  cp -a "$FILE" "${FILE}.bak.${ts}"
  echo "Backup: ${FILE}.bak.${ts}"
fi

cat > "$FILE" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
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

DEV_ENABLED_ENV = "FG_DEV_EVENTS_ENABLED"


def _naive_utc_now():
    # DecisionRecord.created_at is DATETIME naive in your schema
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_compact(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _score_for(threat_level: str) -> float:
    if threat_level == "none":
        return 0.0
    if threat_level in ("critical", "high"):
        return 0.92
    if threat_level == "medium":
        return 0.63
    return 0.35


def _ai_adv_for(kind: str, threat_level: str) -> float:
    # give you useful variance for dashboards/charts
    base = 0.10 if threat_level == "none" else 0.55
    if kind in ("waf", "edge_gw"):
        base += 0.15
    if threat_level in ("critical", "high"):
        base += 0.20
    return min(0.99, base)


@router.post("/emit")
def dev_emit(
    count: int = Query(10, ge=1, le=500),
    kind: Literal["auth_attempt", "waf", "edge_gw", "collector", "info"] = "auth_attempt",
    threat_level: Literal["critical", "high", "medium", "low", "none"] = "none",
    tenant_id: Optional[str] = None,
    source: Optional[str] = None,
    pq_fallback: bool = False,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    # hard gate: never ship a footgun
    if os.getenv(DEV_ENABLED_ENV, "0") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    src = source or kind
    now = _naive_utc_now()

    recs: List[DecisionRecord] = []
    for i in range(count):
        ip = f"10.0.{i % 255}.{(i * 7) % 255}"
        rules = [
            "rule:default_allow" if threat_level == "none" else "rule:block_badness",
            "rule:baseline",
        ]

        request_json: Dict[str, Any] = {
            "kind": kind,
            "seq": i,
            "ts": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "source": src,
            "threat_level": threat_level,
            "ip": ip,
            "ua": "dev-emitter",
            "path": "/login" if kind == "auth_attempt" else "/",
            "method": "POST" if kind in ("auth_attempt", "collector") else "GET",
        }

        event_id = _sha256_hex(_json_compact(request_json))

        anomaly = _score_for(threat_level)
        ai_adv = _ai_adv_for(kind, threat_level)

        # response_json is REQUIRED in your schema, so make it meaningful
        response_json: Dict[str, Any] = {
            "event_id": event_id,
            "decision": "allow" if threat_level == "none" else "block",
            "action_taken": "log_only" if threat_level == "none" else "blocked",
            "summary": f"dev_emit {kind} threat={threat_level}",
            "anomaly_score": anomaly,
            "ai_adversarial_score": ai_adv,
            "pq_fallback": bool(pq_fallback),
            "rules_triggered": rules,
        }

        # Optional diff field (nullable)
        decision_diff_json: Optional[Dict[str, Any]] = None
        if threat_level != "none":
            decision_diff_json = {
                "changes": ["threat_level", "decision"],
                "prev": {"threat_level": "none", "decision": "allow"},
                "curr": {"threat_level": threat_level, "decision": response_json["decision"]},
                "summary": "Synthetic dev diff",
            }

        recs.append(
            DecisionRecord(
                created_at=now,
                tenant_id=tenant_id,
                source=src,
                event_id=event_id,
                event_type=kind,
                threat_level=threat_level,
                anomaly_score=anomaly,
                ai_adversarial_score=ai_adv,
                pq_fallback=bool(pq_fallback),
                rules_triggered_json=rules,          # JSON NOT NULL
                decision_diff_json=decision_diff_json,
                request_json=request_json,           # JSON NOT NULL
                response_json=response_json,         # JSON NOT NULL
            )
        )

    db.add_all(recs)
    db.flush()
    ids = [int(r.id) for r in recs]
    db.commit()

    return {"ok": True, "created": len(ids), "ids": ids[-10:]}
PY

python -m py_compile "$FILE"
echo "✅ Patched + compiled: $FILE"
