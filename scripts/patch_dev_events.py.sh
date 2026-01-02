#!/usr/bin/env bash
set -euo pipefail

FILE="api/dev_events.py"
ts="$(date +%Y%m%d_%H%M%S)"
mkdir -p api

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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _json_compact(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _score_for(threat_level: str) -> int:
    if threat_level == "none":
        return 0
    if threat_level in ("critical", "high"):
        return 90
    if threat_level == "medium":
        return 60
    return 35


def _confidence_for(severity: str) -> float:
    if severity in ("critical", "high"):
        return 0.95
    if severity == "medium":
        return 0.80
    return 0.70


@router.post("/emit")
def dev_emit(
    count: int = Query(10, ge=1, le=500),
    kind: Literal["auth_attempt", "waf", "edge_gw", "collector", "info"] = "auth_attempt",
    severity: Literal["critical", "high", "medium", "low", "info"] = "info",
    threat_level: Literal["critical", "high", "medium", "low", "none"] = "none",
    action_taken: Literal["blocked", "rate_limited", "quarantined", "log_only"] = "log_only",
    tenant_id: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if os.getenv(DEV_ENABLED_ENV, "0") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    src = source or kind
    allowed_cols = set(DecisionRecord.__table__.columns.keys())

    recs: List[DecisionRecord] = []
    now_naive = _naive_utc_now()

    for i in range(count):
        ip = f"10.0.{i % 255}.{(i * 7) % 255}"
        rules = [
            "rule:default_allow" if threat_level == "none" else "rule:block_badness",
            "rule:rate_limit" if action_taken == "rate_limited" else "rule:baseline",
        ]

        payload = {
            "kind": kind,
            "i": i,
            "ts": _utc_iso(),
            "tenant_id": tenant_id,
            "source": src,
            "severity": severity,
            "threat_level": threat_level,
            "action_taken": action_taken,
            "ip": ip,
            "ua": "dev-emitter",
            "rules_triggered": rules,
            "score": _score_for(threat_level),
        }

        payload_json = _json_compact(payload)
        event_id = _sha256_hex(payload_json)
        decision_id = event_id

        data = {
            "event_id": event_id,
            "event_type": kind,
            "source": src,
            "tenant_id": tenant_id,
            "threat_level": threat_level,
            "decision_id": decision_id,
            "decision_json": payload_json,
            "timestamp": now_naive,
            "severity": severity,
            "title": f"{kind} from {src}",
            "summary": f"Dev emit {kind} ({severity}/{threat_level})",
            "action_taken": action_taken,
            "confidence": _confidence_for(severity),
            "fingerprint": event_id,
            "score": float(payload["score"]),
            "rules_triggered": rules,
            "changed_fields": [],
            "action_reason": "dev_emit synthetic event",
        }

        recs.append(DecisionRecord(**{k: v for k, v in data.items() if k in allowed_cols}))

    db.add_all(recs)
    db.flush()
    created_ids = [int(r.id) for r in recs if getattr(r, "id", None) is not None]
    db.commit()

    return {"ok": True, "created": len(created_ids), "ids": created_ids[-10:]}
PY

python -m py_compile "$FILE"
echo "✅ Patched + compiled: $FILE"
