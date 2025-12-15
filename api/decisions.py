from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth import verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord

router = APIRouter(
    prefix="/decisions",
    tags=["decisions"],
    dependencies=[Depends(verify_api_key)],  # auth is consistent now
)

MAX_PAGE_SIZE = 100


def _clamp_page_size(n: int) -> int:
    return max(1, min(MAX_PAGE_SIZE, n))


@router.get("")
def list_decisions(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_PAGE_SIZE),
    tenant_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    threat_level: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None, description="ISO timestamp inclusive"),
    until: Optional[datetime] = Query(None, description="ISO timestamp inclusive"),
) -> dict[str, Any]:
    """
    Returns a paginated list of decisions, newest first.
    Includes high-signal fields only (no giant blobs) for list speed.
    """
    page_size = _clamp_page_size(page_size)

    q = db.query(DecisionRecord)

    if tenant_id:
        q = q.filter(DecisionRecord.tenant_id == tenant_id)
    if source:
        q = q.filter(DecisionRecord.source == source)
    if event_type:
        q = q.filter(DecisionRecord.event_type == event_type)
    if threat_level:
        q = q.filter(DecisionRecord.threat_level == threat_level)
    if since:
        q = q.filter(DecisionRecord.created_at >= since)
    if until:
        q = q.filter(DecisionRecord.created_at <= until)

    total = q.with_entities(func.count(DecisionRecord.id)).scalar() or 0

    rows = (
        q.order_by(DecisionRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "tenant_id": r.tenant_id,
                "source": r.source,
                "event_type": r.event_type,
                "threat_level": r.threat_level,
                "anomaly_score": r.anomaly_score,
                "ai_adversarial_score": r.ai_adversarial_score,
                "pq_fallback": bool(r.pq_fallback),
                "rules_triggered": r.rules_triggered,
                "explain_summary": r.explain_summary,
                "latency_ms": r.latency_ms,
            }
        )

    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{decision_id}")
def get_decision(
    decision_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Full forensic record (includes request/response blobs).
    """
    rec = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.id == decision_id)
        .one_or_none()
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    return {
        "id": rec.id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "tenant_id": rec.tenant_id,
        "source": rec.source,
        "event_type": rec.event_type,
        "enforcement_mode": rec.enforcement_mode,
        "threat_level": rec.threat_level,
        "anomaly_score": rec.anomaly_score,
        "ai_adversarial_score": rec.ai_adversarial_score,
        "pq_fallback": bool(rec.pq_fallback),
        "rules_triggered": rec.rules_triggered,
        "explain_summary": rec.explain_summary,
        "latency_ms": rec.latency_ms,
        "request_payload": rec.request_payload,
        "response_payload": rec.response_payload,
    }
