from __future__ import annotations

import asyncio
import json
import time
from typing import Any, List

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import verify_api_key
from api.db import get_db
from api.db_models import DecisionRecord
from api.decisions import _loads_json_text
from api.ratelimit import rate_limit_guard

# -----------------------------------------------------------------------------
# Query-param normalization helpers
# (prevents empty-string filters + Query(None) objects from leaking into SQL binds)
# -----------------------------------------------------------------------------


def _fg_is_query_obj(v) -> bool:
    try:
        return v.__class__.__name__ == "Query"
    except Exception:
        return False


def _fg_coerce_query_default(v):
    if _fg_is_query_obj(v):
        try:
            return v.default
        except Exception:
            return None
    return v


def _fg_norm_str(v):
    v = _fg_coerce_query_default(v)
    if v is None:
        return None
    try:
        v = str(v)
    except Exception:
        return None
    v = v.strip()
    return v if v else None


def _fg_norm_int(v):
    v = _fg_coerce_query_default(v)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None


def _fg_norm_bool(v):
    v = _fg_coerce_query_default(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    sv = str(v).strip().lower()
    if sv in ("1", "true", "t", "yes", "y", "on"):
        return True
    if sv in ("0", "false", "f", "no", "n", "off"):
        return False
    return None


router = APIRouter(
    prefix="/feed",
    tags=["feed"],
    dependencies=[Depends(rate_limit_guard), Depends(verify_api_key)],
)

# -----------------------------
# Presentation / backfill logic
# -----------------------------


def _sev_from_threat(threat: str | None) -> str:
    t = (threat or "").strip().lower()
    if t in ("critical",):
        return "critical"
    if t in ("high",):
        return "high"
    if t in ("medium",):
        return "medium"
    if t in ("low",):
        return "low"
    return "info"


def _infer_action_taken(decision_diff: Any) -> str:
    """
    Infer a UI-friendly action_taken from decision_diff.
    This is intentionally heuristic because the DB model doesn't store action_taken.
    """
    if not isinstance(decision_diff, dict):
        return "log_only"

    summ = str(decision_diff.get("summary") or "").lower()
    curr = decision_diff.get("curr") or {}
    prev = decision_diff.get("prev") or {}

    # If diff explicitly includes a decision/action, prefer it
    for k in ("decision", "action", "action_taken"):
        v = curr.get(k) or prev.get(k)
        if isinstance(v, str) and v.strip():
            vv = v.strip().lower()
            if vv in ("block", "blocked", "deny", "drop"):
                return "blocked"
            if "rate" in vv:
                return "rate_limited"
            if vv in ("allow", "log", "log_only", "monitor"):
                return "log_only"

    if "block" in summ or "blocked" in summ or "deny" in summ or "drop" in summ:
        return "blocked"
    if "rate" in summ or "throttle" in summ:
        return "rate_limited"

    return "log_only"


def _derive_from_diff(
    diff: Any,
) -> tuple[list[str], float | None, list[str], str | None]:
    """
    Returns: (rules_triggered, score, changed_fields, action_reason)
    """
    if not isinstance(diff, dict):
        return ([], None, [], None)

    prev = diff.get("prev") or {}
    curr = diff.get("curr") or {}
    changes = diff.get("changes") or []

    rules = curr.get("rules_triggered") or prev.get("rules_triggered") or []
    score = curr.get("score")

    changed_fields: list[str] = []
    if isinstance(changes, list):
        for c in changes:
            if isinstance(c, dict) and "field" in c:
                changed_fields.append(str(c["field"]))
            elif isinstance(c, str):
                changed_fields.append(c)

    action_reason = diff.get("summary")
    if action_reason and len(str(action_reason)) > 240:
        action_reason = str(action_reason)[:240] + "…"

    rules_out: list[str] = []
    if isinstance(rules, list):
        rules_out = [str(r) for r in rules][:10]
    elif isinstance(rules, str):
        rules_out = [rules]

    return (
        rules_out,
        score if isinstance(score, (int, float)) else None,
        changed_fields,
        str(action_reason) if action_reason is not None else None,
    )


def _backfill_feed_item(i: dict) -> dict:
    # timestamp
    if not i.get("timestamp"):
        ca = i.get("created_at")
        i["timestamp"] = ca or None

    # severity
    if not i.get("severity"):
        i["severity"] = _sev_from_threat(i.get("threat_level"))

    # action_taken
    if not i.get("action_taken"):
        i["action_taken"] = _infer_action_taken(i.get("decision_diff"))

    # title / summary
    if not i.get("title"):
        et = i.get("event_type") or "event"
        src = i.get("source") or "unknown"
        i["title"] = f"{et} from {src}"

    if not i.get("summary"):
        sev = i.get("severity") or "info"
        thr = i.get("threat_level") or ""
        act = i.get("action_taken") or ""
        i["summary"] = f"{sev}/{thr} {act}".strip()

    # confidence / score
    if i.get("confidence") is None:
        sev = (i.get("severity") or "info").lower()
        i["confidence"] = 0.95 if sev in ("critical", "high") else 0.75

    if i.get("score") is None:
        thr = (i.get("threat_level") or "").lower()
        i["score"] = (
            90 if thr in ("critical", "high") else (60 if thr == "medium" else 0)
        )

    # rules_triggered always list
    if i.get("rules_triggered") is None:
        i["rules_triggered"] = []

    # changed_fields always list
    if i.get("changed_fields") is None:
        i["changed_fields"] = []

    # fingerprint
    if not i.get("fingerprint"):
        i["fingerprint"] = "|".join(
            [
                str(i.get("event_type") or ""),
                str(i.get("source") or ""),
                str(i.get("tenant_id") or ""),
                str(i.get("event_id") or ""),
                str(i.get("threat_level") or ""),
                str(i.get("action_taken") or ""),
            ]
        )

    return i


def _is_actionable(item: dict) -> bool:
    sev = (item.get("severity") or "").lower()
    act = (item.get("action_taken") or "").lower()
    # heuristic: "log_only" + non-high/critical = not actionable
    if act == "log_only" and sev not in ("high", "critical"):
        return False
    return True


# -----------------------------
# API models
# -----------------------------


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

    score: float | None = None
    rules_triggered: List[str] = Field(default_factory=list)
    changed_fields: List[str] = Field(default_factory=list)
    action_reason: str | None = None
    fingerprint: str | None = None

    decision_diff: Any | None = None
    metadata: Any | None = None


class FeedLiveResponse(BaseModel):
    items: List[FeedItem] = Field(default_factory=list)
    next_since_id: int | None = None


# -----------------------------
# Route: /live
# -----------------------------


@router.get("/live", response_model=FeedLiveResponse)
def feed_live(
    db: Session = Depends(get_db),
    # pagination/incremental
    limit: int = Query(default=50, ge=1, le=200),
    since_id: int | None = Query(default=None, ge=0),
    # filters (severity is an alias for threat_level)
    severity: str | None = Query(default=None),
    threat_level: str | None = Query(default=None),
    action_taken: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    q: str | None = Query(
        default=None, description="search event_type/event_id/source"
    ),
    # toggles
    only_changed: bool = Query(default=False),
    only_actionable: bool = Query(default=False),
):
    # --- normalization guardrails ---
    since_id = _fg_norm_int(since_id)
    limit = _fg_norm_int(limit) or limit
    threat_level = _fg_norm_str(threat_level)
    severity = _fg_norm_str(severity)
    action_taken = _fg_norm_str(action_taken)
    source = _fg_norm_str(source)
    tenant_id = _fg_norm_str(tenant_id)
    q = _fg_norm_str(q)
    only_changed = bool(_fg_norm_bool(only_changed))
    only_actionable = bool(_fg_norm_bool(only_actionable))
    # --- end normalization guardrails ---

    qry = db.query(DecisionRecord)

    # alias: severity -> threat_level (DB only has threat_level)
    if (not threat_level) and severity:
        threat_level = severity

    if since_id is not None:
        qry = qry.filter(DecisionRecord.id > since_id)

    if threat_level:
        qry = qry.filter(DecisionRecord.threat_level == threat_level)

    if source:
        qry = qry.filter(DecisionRecord.source == source)

    if tenant_id:
        qry = qry.filter(DecisionRecord.tenant_id == tenant_id)

    # Search only on real columns
    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (DecisionRecord.event_type.ilike(like))
            | (DecisionRecord.event_id.ilike(like))
            | (DecisionRecord.source.ilike(like))
        )

    qry = qry.order_by(DecisionRecord.id.desc()).limit(limit)
    rows = qry.all()

    items: list[FeedItem] = []
    max_id = since_id or 0

    for r in rows:
        rid = int(r.id)
        max_id = max(max_id, rid)

        ts = getattr(r, "created_at", None)
        ts_iso = ts.isoformat() if ts else None

        diff = _loads_json_text(getattr(r, "decision_diff_json", None))
        rules_triggered, score, changed_fields, action_reason = _derive_from_diff(diff)

        item_dict = {
            "id": rid,
            "event_id": getattr(r, "event_id", None),
            "event_type": getattr(r, "event_type", None),
            "source": getattr(r, "source", None),
            "tenant_id": getattr(r, "tenant_id", None),
            "threat_level": getattr(r, "threat_level", None),
            "decision_id": None,
            "timestamp": ts_iso,
            "severity": None,
            "title": None,
            "summary": None,
            "action_taken": None,
            "confidence": None,
            "score": score,
            "rules_triggered": rules_triggered or [],
            "changed_fields": changed_fields or [],
            "action_reason": action_reason,
            "fingerprint": None,
            "decision_diff": diff,
            "metadata": None,
        }

        item_dict = _backfill_feed_item(item_dict)

        # Post-backfill filters (because DB doesn't store these)
        if action_taken and (item_dict.get("action_taken") != action_taken):
            continue

        if only_changed and not item_dict.get("changed_fields"):
            continue

        if only_actionable and not _is_actionable(item_dict):
            continue

        items.append(FeedItem(**item_dict))

    return FeedLiveResponse(items=items, next_since_id=max_id)


# === STREAM BEGIN (do not patch with regex) ===


@router.head("/stream")
def feed_stream_head() -> Response:
    # Headers-only probe for smoke tests / health checks
    return Response(content=b"", media_type="text/event-stream")


@router.get("/stream")
async def feed_stream(
    request: Request,
    db: Session = Depends(get_db),
    # pacing
    interval: float = Query(default=1.0, ge=0.2, le=10.0),
    heartbeat: float = Query(default=10.0, ge=2.0, le=60.0),
    # pagination/incremental
    limit: int = Query(default=50, ge=1, le=200),
    since_id: int | None = Query(default=None, ge=0),
    # filters (severity is an alias for threat_level)
    severity: str | None = Query(default=None),
    threat_level: str | None = Query(default=None),
    action_taken: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    q: str | None = Query(
        default=None, description="search event_type/event_id/source"
    ),
    # toggles
    only_changed: bool = Query(default=False),
    only_actionable: bool = Query(default=False),
):
    """
    Production SSE endpoint:
      - never throws inside generator (prevents 500 'Internal Server Error')
      - emits batches as JSON: {"items":[...], "next_since_id": N}
      - heartbeat ': ping' comments keep proxies happy
      - reuses feed_live() for filtering consistency
    """

    severity = _fg_norm_str(severity)
    threat_level = _fg_norm_str(threat_level)
    action_taken = _fg_norm_str(action_taken)
    source = _fg_norm_str(source)
    tenant_id = _fg_norm_str(tenant_id)
    q = _fg_norm_str(q)

    if severity and not threat_level:
        threat_level = severity

    # normalize ints/bools
    since_id_n = _fg_norm_int(since_id)
    limit_n = _fg_norm_int(limit) or limit
    only_changed_b = bool(_fg_norm_bool(only_changed))
    only_actionable_b = bool(_fg_norm_bool(only_actionable))

    async def gen():
        last_id = since_id_n
        last_hb = time.monotonic()
        yield ": connected\n\n"

        while True:
            try:
                if await request.is_disconnected():
                    break

                resp = feed_live(
                    db=db,
                    limit=limit_n,
                    since_id=last_id,
                    severity=severity,
                    threat_level=threat_level,
                    action_taken=action_taken,
                    source=source,
                    tenant_id=tenant_id,
                    q=q,
                    only_changed=only_changed_b,
                    only_actionable=only_actionable_b,
                )

                payload = {
                    "items": [item.model_dump() for item in resp.items],
                    "next_since_id": resp.next_since_id,
                }

                if resp.next_since_id is not None:
                    last_id = resp.next_since_id
                elif resp.items:
                    try:
                        last_id = max(
                            int(it.id) for it in resp.items if it.id is not None
                        )
                    except Exception:
                        pass

                yield (
                    "data: "
                    + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                    + "\n\n"
                )

                now = time.monotonic()
                if now - last_hb >= heartbeat:
                    last_hb = now
                    yield ": ping\n\n"

            except asyncio.CancelledError:
                break
            except Exception:
                # never explode the stream; emit a comment and keep going
                yield ": error\n\n"

            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream")


# === STREAM END ===
