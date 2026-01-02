from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord
from api.decisions import _loads_json_text
from api.ratelimit import rate_limit_guard


router = APIRouter(
    prefix="/feed",
    tags=["feed"],
    dependencies=[Depends(rate_limit_guard), Depends(verify_api_key)],
)


class FeedItem(BaseModel):
    id: int
    event_id: str | None = None
    event_type: str | None = None
    source: str | None = None
    tenant_id: str | None = None

    threat_level: str | None = None
    decision_id: str | None = None

    timestamp: str | None = None
    severity: str | None = None
    title: str | None = None
    summary: str | None = None
    action_taken: str | None = None
    confidence: float | None = None

    # Helpful derived fields
    score: float | None = None
    rules_triggered: List[str] = Field(default_factory=list)
    changed_fields: List[str] = Field(default_factory=list)
    action_reason: str | None = None
    fingerprint: str | None = None

    # Heavy stuff, keep collapsible in UI
    decision_diff: Any | None = None
    metadata: Any | None = None


class FeedLiveResponse(BaseModel):
    items: List[FeedItem] = Field(default_factory=list)
    next_since_id: int | None = None


def _coerce_str(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _derive_from_diff(diff: Any) -> tuple[list[str], float | None, list[str], str | None]:
    """
    Returns: (rules_triggered, score, changed_fields, action_reason)
    """
    if not isinstance(diff, dict):
        return ([], None, [], None)

    prev = diff.get("prev") or {}
    curr = diff.get("curr") or {}
    changes = diff.get("changes") or []

    # Rules/score often live in prev/curr in your records
    rules = curr.get("rules_triggered") or prev.get("rules_triggered") or []
    score = curr.get("score")
    changed_fields: list[str] = []

    if isinstance(changes, list):
        for c in changes:
            if isinstance(c, dict) and "field" in c:
                changed_fields.append(str(c["field"]))
            elif isinstance(c, str):
                changed_fields.append(c)

    # Tiny human reason: prefer diff summary if present
    action_reason = diff.get("summary")
    if action_reason and len(str(action_reason)) > 240:
        action_reason = str(action_reason)[:240] + "…"

    # Normalize rules list
    rules_out: list[str] = []
    if isinstance(rules, list):
        rules_out = [str(r) for r in rules][:10]
    elif isinstance(rules, str):
        rules_out = [rules]

    return (rules_out, score if isinstance(score, (int, float)) else None, changed_fields, _coerce_str(action_reason))


@router.get("/live", response_model=FeedLiveResponse)
def feed_live(
    db: Session = Depends(get_db),

    # pagination/incremental
    limit: int = Query(default=50, ge=1, le=200),
    since_id: int | None = Query(default=None, ge=0),

    # filters
    severity: str | None = Query(default=None),
    threat_level: str | None = Query(default=None),
    action_taken: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="search title/summary/event_type/event_id"),

    # toggles
    only_changed: bool = Query(default=False),
    only_actionable: bool = Query(default=False),
):
    qry = db.query(DecisionRecord)

    if since_id is not None:
        qry = qry.filter(DecisionRecord.id > since_id)

    if severity:
        qry = qry.filter(DecisionRecord.severity == severity)
    if threat_level:
        qry = qry.filter(DecisionRecord.threat_level == threat_level)
    if action_taken:
        qry = qry.filter(DecisionRecord.action_taken == action_taken)
    if source:
        qry = qry.filter(DecisionRecord.source == source)
    if tenant_id:
        qry = qry.filter(DecisionRecord.tenant_id == tenant_id)

    # crude but effective search across a few columns
    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (DecisionRecord.title.ilike(like))
            | (DecisionRecord.summary.ilike(like))
            | (DecisionRecord.event_type.ilike(like))
            | (DecisionRecord.event_id.ilike(like))
            | (DecisionRecord.decision_id.ilike(like))
        )

    # ordering: newest first for initial load; incremental still fine because we filter id > since
    qry = qry.order_by(DecisionRecord.id.desc()).limit(limit)

    rows = qry.all()

    items: list[FeedItem] = []
    max_id = since_id or 0

    for r in rows:
        max_id = max(max_id, int(r.id))

        diff = _loads_json_text(getattr(r, "decision_diff_json", None))
        meta = _loads_json_text(getattr(r, "metadata_json", None))

        rules_triggered, score, changed_fields, action_reason = _derive_from_diff(diff)

        # actionable heuristic
        actionable = True
        if getattr(r, "action_taken", None) == "log_only" and (getattr(r, "severity", None) not in ("high", "critical")):
            actionable = False

        if only_actionable and not actionable:
            continue

        if only_changed and not changed_fields:
            continue

        fingerprint = f"{getattr(r,'event_type',None)}|{getattr(r,'source',None)}|{getattr(r,'tenant_id',None)}|{getattr(r,'threat_level',None)}|{getattr(r,'action_taken',None)}"

        items.append(
            FeedItem(
                id=int(r.id),
                event_id=_coerce_str(getattr(r, "event_id", None)),
                event_type=_coerce_str(getattr(r, "event_type", None)),
                source=_coerce_str(getattr(r, "source", None)),
                tenant_id=_coerce_str(getattr(r, "tenant_id", None)),

                threat_level=_coerce_str(getattr(r, "threat_level", None)),
                decision_id=_coerce_str(getattr(r, "decision_id", None)),

                timestamp=_coerce_str(getattr(r, "timestamp", None)),
                severity=_coerce_str(getattr(r, "severity", None)),
                title=_coerce_str(getattr(r, "title", None)),
                summary=_coerce_str(getattr(r, "summary", None)),
                action_taken=_coerce_str(getattr(r, "action_taken", None)),
                confidence=getattr(r, "confidence", None),

                score=score,
                rules_triggered=rules_triggered,
                changed_fields=changed_fields,
                action_reason=action_reason,
                fingerprint=fingerprint,

                decision_diff=diff,
                metadata=meta,
            )
        )

    return FeedLiveResponse(items=items, next_since_id=max_id)
