from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes
from api.db import get_db
from api.db_models import DecisionRecord

router = APIRouter(
    prefix="",
    tags=["stats"],
)

Window = Literal["1h", "24h"]

def _cutoff(window: Window) -> datetime:
    now = datetime.now(timezone.utc)
    if window == "1h":
        return now - timedelta(hours=1)
    return now - timedelta(hours=24)

@router.get("/stats", dependencies=[Depends(require_scopes("feed:read"))])
def stats(
    request: Request,
    window: Window = Query(default="24h"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    cutoff = _cutoff(window)

    # If your column names differ, adjust here:
    # DecisionRecord.created_at / ts / decided_at, DecisionRecord.severity, DecisionRecord.event_type, DecisionRecord.rule_hits_json, etc.
    ts_col = getattr(DecisionRecord, "created_at", None) or getattr(DecisionRecord, "ts", None)
    if ts_col is None:
        # fallback: no time window possible
        cutoff = datetime.fromtimestamp(0, tz=timezone.utc)
        ts_col = getattr(DecisionRecord, "id")  # cheap placeholder

    sev_col = getattr(DecisionRecord, "severity", None)
    event_col = getattr(DecisionRecord, "event_type", None)

    base = db.query(DecisionRecord).filter(ts_col >= cutoff)

    total = base.count()

    by_sev: Dict[str, int] = {}
    if sev_col is not None:
        rows = (
            db.query(sev_col, func.count().label("c"))
            .filter(ts_col >= cutoff)
            .group_by(sev_col)
            .all()
        )
        by_sev = {str(sev): int(c) for sev, c in rows}

    top_event_types: List[Dict[str, Any]] = []
    if event_col is not None:
        rows = (
            db.query(event_col, func.count().label("c"))
            .filter(ts_col >= cutoff)
            .group_by(event_col)
            .order_by(func.count().desc())
            .limit(10)
            .all()
        )
        top_event_types = [{"event_type": str(et), "count": int(c)} for et, c in rows]

    # Top “rules” is tricky if you store rule hits as JSON.
    # Minimal MVP: count by rule_name if you have a column like `top_rule` or `rule_id`.
    top_rules: List[Dict[str, Any]] = []
    rule_col = getattr(DecisionRecord, "rule", None) or getattr(DecisionRecord, "rule_id", None)
    if rule_col is not None:
        rows = (
            db.query(rule_col, func.count().label("c"))
            .filter(ts_col >= cutoff)
            .group_by(rule_col)
            .order_by(func.count().desc())
            .limit(10)
            .all()
        )
        top_rules = [{"rule": str(r), "count": int(c)} for r, c in rows]

    return {
        "status": "ok",
        "window": window,
        "cutoff": cutoff.isoformat(),
        "total": total,
        "by_severity": by_sev,
        "top_event_types": top_event_types,
        "top_rules": top_rules,
        "auth_enabled": getattr(request.app.state, "auth_enabled", None),
    }