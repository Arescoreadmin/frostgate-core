# api/telemetry.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Optional, Tuple

# production knobs
MAX_FUTURE_SKEW_SEC = 300  # allow client clock up to 5 minutes ahead
MAX_PAST_AGE_SEC = 30 * 24 * 3600  # accept events up to 30 days old


@dataclass(frozen=True)
class EventTimeResult:
    event_ts: Optional[datetime]
    event_ts_valid: bool
    event_ts_reason: Optional[str]
    ingested_at: datetime
    event_age_ms: Optional[int]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso8601_to_utc(ts_str: str) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Strict-ish ISO8601 parser without extra deps.
    Accepts 'Z' suffix and offsets. Rejects naive datetimes.
    Returns: (dt_utc or None, reason_if_invalid)
    """
    if not ts_str:
        return None, "missing"

    s = ts_str.strip()
    # normalize Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None, "parse_error"

    if dt.tzinfo is None:
        return None, "naive_timestamp"

    return dt.astimezone(timezone.utc), None


def compute_event_time(
    ts_str: Optional[str], *, ingested_at: Optional[datetime] = None
) -> EventTimeResult:
    ing = ingested_at or utcnow()

    if not ts_str:
        return EventTimeResult(
            event_ts=None,
            event_ts_valid=False,
            event_ts_reason="missing",
            ingested_at=ing,
            event_age_ms=None,
        )

    dt_utc, reason = _parse_iso8601_to_utc(ts_str)
    if dt_utc is None:
        return EventTimeResult(
            event_ts=None,
            event_ts_valid=False,
            event_ts_reason=reason or "invalid",
            ingested_at=ing,
            event_age_ms=None,
        )

    delta_sec = (ing - dt_utc).total_seconds()

    # future skew check
    if delta_sec < -MAX_FUTURE_SKEW_SEC:
        return EventTimeResult(
            event_ts=None,
            event_ts_valid=False,
            event_ts_reason="future_timestamp",
            ingested_at=ing,
            event_age_ms=None,
        )

    # too old
    if delta_sec > MAX_PAST_AGE_SEC:
        return EventTimeResult(
            event_ts=None,
            event_ts_valid=False,
            event_ts_reason="too_old",
            ingested_at=ing,
            event_age_ms=None,
        )

    return EventTimeResult(
        event_ts=dt_utc,
        event_ts_valid=True,
        event_ts_reason=None,
        ingested_at=ing,
        event_age_ms=int(delta_sec * 1000),
    )


class RequestTimer:
    """
    Monotonic request timer. Use as:
      timer = RequestTimer()
      ...
      latency_ms = timer.elapsed_ms()
    """

    __slots__ = ("_t0",)

    def __init__(self) -> None:
        self._t0 = perf_counter_ns()

    def elapsed_ms(self) -> int:
        return int((perf_counter_ns() - self._t0) / 1_000_000)
