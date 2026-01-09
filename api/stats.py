# api/stats.py
from __future__ import annotations

from collections import Counter
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth_scopes import require_api_key_always
from api.db import get_db
from api.db_models import DecisionRecord


# ----------------------------
# small utilities
# ----------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
    try:
        s = str(v).strip()
        if not s:
            return None
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


def _as_dict(v: Any) -> Optional[dict]:
    """Accept dict or JSON string; return dict or None."""
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            out = json.loads(s)
            return out if isinstance(out, dict) else None
        except Exception:
            return None
    return None


def _event_time(rec: Any) -> Optional[datetime]:
    """
    Prefer event timestamp from request_json to avoid demo drift:
      - seed script sets request_json["timestamp"]
      - DB created_at is insert time (often clustered), which breaks windows/trends
    Fallback to DecisionRecord.created_at.
    """
    req = _get_attr(rec, "request_json", default=None)
    d = _as_dict(req)
    if d:
        ts = d.get("timestamp") or d.get("ts")
        dt = _coerce_dt(ts)
        if dt:
            return dt

        # Sometimes event timestamp is nested
        ev = d.get("event")
        evd = _as_dict(ev) if ev is not None else None
        if evd:
            ts2 = evd.get("timestamp") or evd.get("ts")
            dt2 = _coerce_dt(ts2)
            if dt2:
                return dt2

    return _coerce_dt(_get_attr(rec, "created_at", default=None))


# ----------------------------
# extraction: rules + src ip
# ----------------------------


def _extract_rules_from_response_obj(response_obj: Any) -> list[str]:
    if response_obj is None:
        return []
    if isinstance(response_obj, str):
        try:
            response_obj = json.loads(response_obj)
        except Exception:
            return []
    if not isinstance(response_obj, dict):
        return []
    try:
        rules = (response_obj.get("explain") or {}).get("rules_triggered", [])
        if isinstance(rules, list):
            return [str(r).strip() for r in rules if str(r).strip()]
    except Exception:
        pass
    return []


def _extract_rules(rec: Any) -> list[str]:
    """
    Primary source: rules_triggered_json column.
    Fallback: response_json.explain.rules_triggered.
    """
    raw = _get_attr(rec, "rules_triggered_json", default=None)

    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        return [x.strip() for x in s.split(",") if x.strip()]

    resp = _get_attr(rec, "response_json", default=None)
    return _extract_rules_from_response_obj(resp)


def _extract_source_ip_from_request_obj(request_obj: Any) -> Optional[str]:
    """
    Pull a likely attacker/source IP from stored request_json.
    We look inside request_json["event"] first, then request_json itself.
    """
    d = _as_dict(request_obj)
    if not d:
        return None

    event = d.get("event")
    evd = _as_dict(event) if event is not None else None

    def pick(m: dict) -> Optional[str]:
        for k in ("src_ip", "source_ip", "source_ip_addr", "ip", "remote_ip"):
            v = m.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return None

    ip = pick(evd) if evd else None
    if ip:
        return ip
    return pick(d)


# ----------------------------
# summary helpers
# ----------------------------


def _risk_score_from_counts(threat_counts: Dict[str, int]) -> int:
    """
    Weighted risk score (0-100) derived from threat counts.
    Weights: none=0, low=20, medium=60, high=100.
    """
    none = int(threat_counts.get("none", 0))
    low = int(threat_counts.get("low", 0))
    med = int(threat_counts.get("medium", 0))
    high = int(threat_counts.get("high", 0))
    total = none + low + med + high
    if total <= 0:
        return 0
    score = (none * 0 + low * 20 + med * 60 + high * 100) / total
    score = max(0.0, min(100.0, score))
    return int(round(score))


def _trend_flag(decisions_24h: int, decisions_7d: int) -> str:
    """
    Compare last 24h to 7d daily-average baseline:
      spike  = > 1.25x baseline
      drop   = < 0.75x baseline
      steady = otherwise
    """
    d24 = int(decisions_24h or 0)
    d7 = int(decisions_7d or 0)
    baseline = d7 / 7.0 if d7 > 0 else 0.0

    if baseline <= 0:
        return "spike" if d24 > 0 else "steady"

    ratio = d24 / baseline
    if ratio > 1.25:
        return "spike"
    if ratio < 0.75:
        return "drop"
    return "steady"


def _pick_most_active_rule(top_rules: List["TopItem"]) -> Optional[str]:
    """
    Prefer a meaningful (non-noise) rule for summary display.
    Fall back to the true top rule if everything is noise.
    """
    if not top_rules:
        return None

    noise = {
        "rule:default_allow",
        "default_allow",
        "rule:allow",
        "allow",
    }

    for item in top_rules:
        name = (item.name or "").strip()
        if name and name.lower() not in noise:
            return name

    return top_rules[0].name or None


def _pick_top_event_type(top_events: List["TopItem"]) -> Optional[str]:
    """
    Buyers don't care that 'auth' is loud. They care that 'auth.bruteforce' is happening.
    Select most important event type using priorities; fallback to count.
    """
    if not top_events:
        return None

    counts = {str(it.name): int(it.count) for it in top_events if it.name}

    priorities: List[str] = [
        "auth.bruteforce",
        "auth.failed",
        "auth.password_spray",
        "rce.attempt",
        "malware.detected",
        "c2.beacon",
        "lateral_movement",
        "privilege_escalation",
        "exfiltration",
    ]

    for p in priorities:
        if p in counts and counts[p] > 0:
            return p

    return top_events[0].name or None


def _headline(
    trend: str, risk_24h: int, top_event: Optional[str], top_rule: Optional[str]
) -> str:
    """
    One-line demo saver: what a human should feel when they see this.
    """
    if risk_24h <= 0:
        return "Stable: no detected threats"

    if top_event and "bruteforce" in top_event.lower():
        return (
            "SSH brute force spike detected"
            if trend == "spike"
            else "SSH brute force activity detected"
        )

    if risk_24h >= 70:
        return f"High risk: investigate {top_event or (top_rule or 'recent activity')}"

    if trend == "spike":
        return f"Spike: increased {top_event or (top_rule or 'security activity')}"

    if trend == "drop":
        return f"Drop: reduced {top_event or (top_rule or 'security activity')}"

    return f"Steady: {top_event or (top_rule or 'security activity')} observed"


# ----------------------------
# response models
# ----------------------------


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

    top_sources_24h: List[TopItem] = Field(default_factory=list)
    unique_source_ips_24h: int = 0
    high_threat_rate_1h: float = 0.0

    avg_latency_ms_24h: float = 0.0
    pct_high_medium_24h: float = 0.0


class StatsSummaryResponse(BaseModel):
    generated_at: str

    risk_score_24h: int = Field(ge=0, le=100)
    risk_score_1h: int = Field(ge=0, le=100)

    most_active_rule: Optional[str] = None
    top_event_type: Optional[str] = None

    high_threat_rate: float = 0.0
    unique_ips: int = 0

    trend_flag: str = Field(pattern="^(spike|steady|drop)$")
    headline: str


router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(require_api_key_always)],
)


# ----------------------------
# Core compute
# ----------------------------


class _Computed:
    def __init__(
        self,
        now: datetime,
        decisions_1h: int,
        decisions_24h: int,
        decisions_7d: int,
        threat_counts_24h: ThreatCounts,
        top_event_types_24h: List[TopItem],
        top_rules_24h: List[TopItem],
        top_sources_24h: List[TopItem],
        unique_source_ips_24h: int,
        high_threat_rate_1h: float,
        avg_latency_ms_24h: float,
        pct_high_medium_24h: float,
        threat_counts_1h: Dict[str, int],
    ):
        self.now = now
        self.decisions_1h = decisions_1h
        self.decisions_24h = decisions_24h
        self.decisions_7d = decisions_7d
        self.threat_counts_24h = threat_counts_24h
        self.top_event_types_24h = top_event_types_24h
        self.top_rules_24h = top_rules_24h
        self.top_sources_24h = top_sources_24h
        self.unique_source_ips_24h = unique_source_ips_24h
        self.high_threat_rate_1h = high_threat_rate_1h
        self.avg_latency_ms_24h = avg_latency_ms_24h
        self.pct_high_medium_24h = pct_high_medium_24h
        self.threat_counts_1h = threat_counts_1h


def _compute_stats(db: Session) -> _Computed:
    now = _utcnow()
    cut_1h = now - timedelta(hours=1)
    cut_24h = now - timedelta(hours=24)
    cut_7d = now - timedelta(days=7)

    # Pull a reasonable slice. We'll classify by _event_time(rec), not just created_at.
    try:
        q = db.query(DecisionRecord)
        if hasattr(DecisionRecord, "created_at"):
            q = q.filter(
                DecisionRecord.created_at >= (cut_7d - timedelta(days=2))
            )  # extra buffer
        records = q.all()
    except Exception:
        records = []

    d1h = d24h = d7d = 0

    threat_counter_24h: Counter[str] = Counter()
    event_counter_24h: Counter[str] = Counter()
    rules_counter_24h: Counter[str] = Counter()
    source_counter_24h: Counter[str] = Counter()
    src_ips_24h: Set[str] = set()

    threat_counter_1h: Counter[str] = Counter()
    total_1h = 0
    high_1h = 0

    latency_sum = 0
    latency_n = 0

    high_med_24h = 0
    total_24h = 0

    for rec in records:
        created = _event_time(rec) or now

        if created >= cut_7d:
            d7d += 1

        if created >= cut_24h:
            d24h += 1
            total_24h += 1

            tl = (
                str(_get_attr(rec, "threat_level", default="none") or "none")
                .lower()
                .strip()
            )
            if tl not in ("none", "low", "medium", "high"):
                tl = "none"
            threat_counter_24h[tl] += 1
            if tl in ("high", "medium"):
                high_med_24h += 1

            et = str(
                _get_attr(rec, "event_type", default="unknown") or "unknown"
            ).strip()
            event_counter_24h[et or "unknown"] += 1

            src = str(_get_attr(rec, "source", default="unknown") or "unknown").strip()
            source_counter_24h[src or "unknown"] += 1

            reqj = _get_attr(rec, "request_json", default=None)
            ip = _extract_source_ip_from_request_obj(reqj)
            if ip:
                src_ips_24h.add(ip)

            for r in _extract_rules(rec):
                rules_counter_24h[r] += 1

            lat = _get_attr(rec, "latency_ms", default=None)
            try:
                if lat is not None:
                    latency_sum += int(lat)
                    latency_n += 1
            except Exception:
                pass

        if created >= cut_1h:
            d1h += 1
            total_1h += 1
            tl1 = (
                str(_get_attr(rec, "threat_level", default="none") or "none")
                .lower()
                .strip()
            )
            if tl1 not in ("none", "low", "medium", "high"):
                tl1 = "none"
            threat_counter_1h[tl1] += 1
            if tl1 == "high":
                high_1h += 1

    avg_latency = (latency_sum / latency_n) if latency_n else 0.0
    pct_high_med_24h = (high_med_24h / total_24h * 100.0) if total_24h else 0.0
    high_rate_1h = (high_1h / total_1h * 100.0) if total_1h else 0.0

    threat_24h = ThreatCounts(
        none=int(threat_counter_24h.get("none", 0)),
        low=int(threat_counter_24h.get("low", 0)),
        medium=int(threat_counter_24h.get("medium", 0)),
        high=int(threat_counter_24h.get("high", 0)),
    )

    top_event_types = [
        TopItem(name=k, count=int(v)) for k, v in event_counter_24h.most_common(10)
    ]
    top_rules = [
        TopItem(name=k, count=int(v)) for k, v in rules_counter_24h.most_common(10)
    ]
    top_sources = [
        TopItem(name=k, count=int(v)) for k, v in source_counter_24h.most_common(10)
    ]

    return _Computed(
        now=now,
        decisions_1h=d1h,
        decisions_24h=d24h,
        decisions_7d=d7d,
        threat_counts_24h=threat_24h,
        top_event_types_24h=top_event_types,
        top_rules_24h=top_rules,
        top_sources_24h=top_sources,
        unique_source_ips_24h=int(len(src_ips_24h)),
        high_threat_rate_1h=float(round(high_rate_1h, 2)),
        avg_latency_ms_24h=float(round(avg_latency, 2)),
        pct_high_medium_24h=float(round(pct_high_med_24h, 2)),
        threat_counts_1h=dict(threat_counter_1h),
    )


# ----------------------------
# Routes
# ----------------------------


@router.get("", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    c = _compute_stats(db)
    return StatsResponse(
        generated_at=_iso(c.now),
        decisions_1h=c.decisions_1h,
        decisions_24h=c.decisions_24h,
        decisions_7d=c.decisions_7d,
        threat_counts_24h=c.threat_counts_24h,
        top_event_types_24h=c.top_event_types_24h,
        top_rules_24h=c.top_rules_24h,
        top_sources_24h=c.top_sources_24h,
        unique_source_ips_24h=int(c.unique_source_ips_24h),
        high_threat_rate_1h=float(c.high_threat_rate_1h),
        avg_latency_ms_24h=float(c.avg_latency_ms_24h),
        pct_high_medium_24h=float(c.pct_high_medium_24h),
    )


@router.get("/summary", response_model=StatsSummaryResponse)
def get_stats_summary(db: Session = Depends(get_db)) -> StatsSummaryResponse:
    """
    Marketing-friendly summary payload for dashboard headers.
    Built from the same underlying computation to avoid drift.
    """
    c = _compute_stats(db)

    threat_counts_24h = c.threat_counts_24h.model_dump()
    risk_24h = _risk_score_from_counts(threat_counts_24h)

    threat_counts_1h: Dict[str, int] = {
        "none": int(c.threat_counts_1h.get("none", 0)),
        "low": int(c.threat_counts_1h.get("low", 0)),
        "medium": int(c.threat_counts_1h.get("medium", 0)),
        "high": int(c.threat_counts_1h.get("high", 0)),
    }
    risk_1h = _risk_score_from_counts(threat_counts_1h)

    most_active_rule = _pick_most_active_rule(c.top_rules_24h)
    top_event_type = _pick_top_event_type(c.top_event_types_24h)

    trend = _trend_flag(int(c.decisions_24h), int(c.decisions_7d))
    headline = _headline(trend, risk_24h, top_event_type, most_active_rule)

    return StatsSummaryResponse(
        generated_at=_iso(c.now),
        risk_score_24h=risk_24h,
        risk_score_1h=risk_1h,
        most_active_rule=most_active_rule,
        top_event_type=top_event_type,
        high_threat_rate=float(round(c.high_threat_rate_1h, 2)),
        unique_ips=int(c.unique_source_ips_24h),
        trend_flag=trend,
        headline=headline,
    )
