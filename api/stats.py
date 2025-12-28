# api/stats.py
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import require_api_key_always
from api.db import get_db
from api.db_models import DecisionRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        # normalize naive -> utc
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
    # string? best-effort parse iso
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _extract_rules(rec: Any) -> List[str]:
    """
    Be tolerant: rules may be stored as rules_triggered / rule_hits / rules,
    possibly as list or JSON-ish string.
    """
    raw = _get_attr(rec, "rules_triggered", "rule_hits", "rules", default=None)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        # try JSON list
        if s.startswith("[") and s.endswith("]"):
            try:
                import json

                arr = json.loads(s)
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        # fallback: CSV-ish
        return [x.strip() for x in s.split(",") if x.strip()]
    # unknown type
    try:
        return [str(raw).strip()] if str(raw).strip() else []
    except Exception:
        return []


class TopItem(BaseModel):
    name: str
    count: int


class ThreatCounts(BaseModel):
    none: int = 0
    low: int = 0
    medium: int = 0
    high: int = 0


class StatsResponse(BaseModel):
    generated_at: str

    decisions_1h: int
    decisions_24h: int
    decisions_7d: int

    threat_counts_24h: ThreatCounts
    top_event_types_24h: List[TopItem] = Field(default_factory=list)
    top_rules_24h: List[TopItem] = Field(default_factory=list)

    avg_latency_ms_24h: float = 0.0
    pct_high_medium_24h: float = 0.0


router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(require_api_key_always)],
)


@router.get("", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    now = _utcnow()
    cut_1h = now - timedelta(hours=1)
    cut_24h = now - timedelta(hours=24)
    cut_7d = now - timedelta(days=7)

    # Pull last 7d decisions and compute everything from that slice.
    # This avoids cross-DB date math issues and keeps it simple/reliable.
    try:
        q = db.query(DecisionRecord)
        if hasattr(DecisionRecord, "created_at"):
            q = q.filter(DecisionRecord.created_at >= cut_7d)  # type: ignore[attr-defined]
        records = q.all()
    except Exception:
        # If DB is unavailable, return safe zeros (MVP behavior: don't 500 your dashboard)
        records = []

    # Partition by time
    d1h = 0
    d24h = 0
    d7d = 0

    threat_counter_24h: Counter[str] = Counter()
    event_counter_24h: Counter[str] = Counter()
    rules_counter_24h: Counter[str] = Counter()

    latency_sum = 0
    latency_n = 0

    high_med = 0
    total_24h = 0

    for rec in records:
        created = _coerce_dt(_get_attr(rec, "created_at", default=None)) or now
        if created >= cut_7d:
            d7d += 1
        if created >= cut_24h:
            d24h += 1
            total_24h += 1

            # threat counts
            tl = str(_get_attr(rec, "threat_level", default="none") or "none").lower().strip()
            if tl not in ("none", "low", "medium", "high"):
                tl = "none"
            threat_counter_24h[tl] += 1
            if tl in ("high", "medium"):
                high_med += 1

            # event types
            et = str(_get_attr(rec, "event_type", default="unknown") or "unknown").strip()
            event_counter_24h[et or "unknown"] += 1

            # rules
            for r in _extract_rules(rec):
                rules_counter_24h[r] += 1

            # latency
            lat = _get_attr(rec, "latency_ms", default=None)
            try:
                if lat is not None:
                    latency_sum += int(lat)
                    latency_n += 1
            except Exception:
                pass

        if created >= cut_1h:
            d1h += 1

    avg_latency = (latency_sum / latency_n) if latency_n else 0.0
    pct_high_med = (high_med / total_24h * 100.0) if total_24h else 0.0

    threat = ThreatCounts(
        none=int(threat_counter_24h.get("none", 0)),
        low=int(threat_counter_24h.get("low", 0)),
        medium=int(threat_counter_24h.get("medium", 0)),
        high=int(threat_counter_24h.get("high", 0)),
    )

    top_event_types = [TopItem(name=k, count=int(v)) for k, v in event_counter_24h.most_common(10)]
    top_rules = [TopItem(name=k, count=int(v)) for k, v in rules_counter_24h.most_common(10)]

    return StatsResponse(
        generated_at=_iso(now),
        decisions_1h=d1h,
        decisions_24h=d24h,
        decisions_7d=d7d,
        threat_counts_24h=threat,
        top_event_types_24h=top_event_types,
        top_rules_24h=top_rules,
        avg_latency_ms_24h=float(round(avg_latency, 2)),
        pct_high_medium_24h=float(round(pct_high_med, 2)),
    )
