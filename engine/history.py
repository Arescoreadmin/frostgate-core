from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Any, List, Optional


_MAX_HISTORY = 1000

_history: Deque[Dict[str, Any]] = deque(maxlen=_MAX_HISTORY)


def record_decision(
    *,
    tenant_id: str,
    source: str,
    threat_level: str,
    rules_triggered: List[str],
    anomaly_score: float,
    ai_adv_score: float,
    pq_fallback: bool,
    clock_drift_ms: int,
) -> None:
    """Record a decision in in-memory history buffer."""
    _history.appendleft(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "source": source,
            "threat_level": threat_level,
            "rules_triggered": list(rules_triggered),
            "anomaly_score": anomaly_score,
            "ai_adversarial_score": ai_adv_score,
            "pq_fallback": pq_fallback,
            "clock_drift_ms": clock_drift_ms,
        }
    )


def list_decisions(
    *,
    tenant_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return recent decisions, optionally filtered by tenant."""
    items: List[Dict[str, Any]] = list(_history)

    if tenant_id is not None:
        items = [d for d in items if d["tenant_id"] == tenant_id]

    return items[:limit]
