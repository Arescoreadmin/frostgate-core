
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes, verify_api_key, require_api_key_always
from api.db import get_db
from api.db_models import DecisionRecord
from api.decisions import _loads_json_text
from api.ratelimit import rate_limit_guard  # ← ADD THIS

router = APIRouter(
    prefix="/feed",
    tags=["feed"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_scopes("feed:read")),
        Depends(rate_limit_guard),
    ],
)

Severity = Literal["info", "low", "medium", "high", "critical"]


class FeedItem(BaseModel):
    id: Optional[int] = None
    event_id: Optional[str] = None
    event_type: Optional[str] = None
    source: Optional[str] = None
    tenant_id: Optional[str] = None
    threat_level: Optional[str] = None
    decision_id: str
    decision_diff: object | None = None
    timestamp: str
    severity: Severity
    title: str
    summary: str
    action_taken: str
    confidence: float = Field(ge=0.0, le=1.0)


class FeedLiveResponse(BaseModel):
    items: List[FeedItem]


def _severity_from_threat(threat: str) -> Severity:
    v = (threat or "").lower()
    if v in ("high",):
        return "high"
    if v in ("medium",):
        return "medium"
    if v in ("low",):
        return "low"
    if v in ("critical",):
        return "critical"
    return "info"


def _action_from_record(r: DecisionRecord) -> str:
    # Best-effort summary of what was done
    # If response_obj exists and has mitigations, use first action; else log_only.
    resp = getattr(r, "response_obj", None) or {}
    try:
        mitigations = resp.get("mitigations") or []
        if mitigations:
            return str(mitigations[0].get("action") or "log_only")
    except Exception:
        pass
    return "log_only"


@router.get("/live", dependencies=[Depends(require_scopes("feed:read"))], response_model=FeedLiveResponse)
def feed_live(
    _auth=Depends(require_api_key_always), limit: int = Query(10, ge=1, le=200),
    db: Session = Depends(get_db),
) -> FeedLiveResponse:
    # Newest-first, stable
    q = (
        db.query(DecisionRecord)
        .order_by(getattr(DecisionRecord, "created_at").desc())
        .limit(limit)
    )
    rows = list(q)

    items: List[FeedItem] = []
    for r in rows:
        created_at = getattr(r, "created_at", None)
        ts = created_at.isoformat() if created_at is not None else ""

        threat = getattr(r, "threat_level", "info") or "info"
        severity = _severity_from_threat(str(threat))

        event_type = getattr(r, "event_type", "") or ""
        source = getattr(r, "source", "") or ""
        summary = getattr(r, "explain_summary", "") or ""

        decision_id = str(getattr(r, "event_id", ""))  # stable id in MVP

        items.append(
            FeedItem(
                decision_id=decision_id,
                decision_diff=_loads_json_text(getattr(r, "decision_diff_json", None)),

        id=getattr(r, 'id', None),
        event_id=getattr(r, 'event_id', None),
        event_type=getattr(r, 'event_type', None),
        source=getattr(r, 'source', None),
        tenant_id=getattr(r, 'tenant_id', None),
        threat_level=getattr(r, 'threat_level', None),
                timestamp=ts,
                severity=severity,
                title=f"{event_type or 'event'} from {source or 'unknown'}",
                summary=summary or f"Decision {severity} for {source or 'unknown'}",
                action_taken=_action_from_record(r),
                confidence=0.80 if severity in ("high", "medium") else 0.60,
            )
        )

    return FeedLiveResponse(items=items)
