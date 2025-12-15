from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.types import JSON  # portable JSON type across SQLite/Postgres

from api.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_jsonable(obj: Any) -> Any:
    """
    Convert request/response objects into JSON-safe dict/list primitives.
    Works with Pydantic v1/v2, dicts, and arbitrary objects.
    """
    if obj is None:
        return None

    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except TypeError:
            return json.loads(json.dumps(obj.model_dump(), default=str))

    # Pydantic v1
    if hasattr(obj, "dict"):
        return json.loads(json.dumps(obj.dict(), default=str))

    # Already JSON-safe
    if isinstance(obj, (dict, list, str, int, float, bool)):
        return obj

    # Last resort: stringify anything weird
    return json.loads(json.dumps(obj, default=str))


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    # Request-side
    tenant_id = Column(String(255), nullable=True, index=True)
    source = Column(String(255), nullable=True, index=True)
    event_type = Column(String(64), nullable=True, index=True)
    enforcement_mode = Column(String(64), nullable=True)

    # Decision surface
    threat_level = Column(String(32), nullable=False, index=True)
    anomaly_score = Column(Float, nullable=True)
    ai_adversarial_score = Column(Float, nullable=True)
    pq_fallback = Column(Boolean, nullable=False, default=False)

    # Rules / explainability
    rules_triggered = Column(JSON, nullable=True)
    explain_summary = Column(Text, nullable=True)

    # Latency
    latency_ms = Column(Integer, nullable=True)

    # Raw payloads (audit)
    request_payload = Column(JSON, nullable=True)
    response_payload = Column(JSON, nullable=True)

    # Helpful compound indexes for common queries
    __table_args__ = (
        Index("ix_decisions_tenant_created", "tenant_id", "created_at"),
        Index("ix_decisions_tenant_threat", "tenant_id", "threat_level"),
    )

    @classmethod
    def from_request_and_response(
        cls,
        request: Any,
        response: Any,
        latency_ms: Optional[int] = None,
        enforcement_mode: Optional[str] = None,
    ) -> "DecisionRecord":
        # Request bits
        tenant_id = getattr(request, "tenant_id", None)
        source = getattr(request, "source", None)

        payload = getattr(request, "payload", None) or {}
        if not isinstance(payload, dict):
            try:
                payload = dict(payload)
            except Exception:
                payload = {}

        event_type = payload.get("event_type")

        # Response bits
        threat_level = getattr(response, "threat_level", None) or "unknown"
        ai_adv_score = getattr(response, "ai_adversarial_score", None)
        pq_fallback = bool(getattr(response, "pq_fallback", False))

        explain = getattr(response, "explain", None)
        anomaly_score = getattr(explain, "anomaly_score", None) if explain else None
        explain_summary = getattr(explain, "summary", None) if explain else None
        rules_triggered = getattr(explain, "rules_triggered", None) if explain else None

        return cls(
            tenant_id=tenant_id,
            source=source,
            event_type=event_type,
            enforcement_mode=enforcement_mode,
            threat_level=threat_level,
            anomaly_score=anomaly_score,
            ai_adversarial_score=ai_adv_score,
            pq_fallback=pq_fallback,
            rules_triggered=rules_triggered,
            explain_summary=explain_summary,
            latency_ms=latency_ms,
            request_payload=to_jsonable(request),
            response_payload=to_jsonable(response),
        )

    def to_public(self) -> dict[str, Any]:
        """
        Lightweight shape for /decisions list views.
        Keeps sensitive raw payloads out of the default response.
        """
        rules = self.rules_triggered
        if rules is None:
            rules = []
        elif not isinstance(rules, list):
            rules = [rules]

        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "event_type": self.event_type,
            "threat_level": self.threat_level,
            "anomaly_score": self.anomaly_score,
            "ai_adversarial_score": self.ai_adversarial_score,
            "pq_fallback": bool(self.pq_fallback),
            "rules_triggered": rules,
            "explain_summary": self.explain_summary,
            "latency_ms": self.latency_ms,
        }
