# api/db_models.py
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.sql import func

log = logging.getLogger("frostgate.db")


# -----------------------------
# Base
# -----------------------------
class Base(DeclarativeBase):
    pass


# -----------------------------
# Helpers
# -----------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(o: Any) -> Any:
    """
    Safe JSON serializer for things that show up in FastAPI/Pydantic payloads.
    - datetime/date -> ISO8601
    - bytes -> utf-8 (lossy-safe)
    - fallback -> str(o)
    """
    if isinstance(o, (datetime, date)):
        if isinstance(o, datetime) and o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.isoformat()
    if isinstance(o, bytes):
        try:
            return o.decode("utf-8", errors="replace")
        except Exception:
            return str(o)
    return str(o)


def json_dumps(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def json_loads(s: Optional[str], default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# -----------------------------
# API Key storage (DB-backed auth)
# -----------------------------
def hash_api_key(raw: str) -> str:
    raw_b = (raw or "").encode("utf-8")
    return hashlib.sha256(raw_b).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


class ApiKey(Base):
    """
    DB-backed API keys.
    Store only hash, never raw key.
    """
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # sha256(raw_key) hex
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # comma-separated scopes
    scopes_csv: Mapped[str] = mapped_column(Text, nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", index=True)

    @property
    def scopes_list(self) -> List[str]:
        if not self.scopes_csv:
            return []
        return [s.strip() for s in self.scopes_csv.split(",") if s.strip()]

    def set_scopes(self, scopes: List[str]) -> None:
        self.scopes_csv = ",".join(sorted({s.strip() for s in scopes if s and s.strip()}))


# -----------------------------
# Decision logging (MATCHES YOUR EXISTING TABLE)
# Table: public.decisions
# Columns: rules_triggered_json (text), request_json (text), response_json (text)
# -----------------------------
class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Your DB shows event_id is TEXT (not varchar)
    event_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # Your DB shows event_type varchar(64), threat_level varchar(32)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    threat_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    ai_adversarial_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    pq_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # These are TEXT columns in your DB, storing JSON strings
    rules_triggered_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explain_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    request_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Convenience accessors (so your API code can treat them like dict/list) ----
    @property
    def rules_triggered(self) -> List[str]:
        return json_loads(self.rules_triggered_json, default=[])

    @property
    def request(self) -> Optional[Dict[str, Any]]:
        return json_loads(self.request_json, default=None)

    @property
    def response(self) -> Optional[Dict[str, Any]]:
        return json_loads(self.response_json, default=None)

    def to_dict(self) -> Dict[str, Any]:
        """
        Clean serialization for API responses (avoids SQLAlchemy internal junk).
        """
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "threat_level": self.threat_level,
            "anomaly_score": float(self.anomaly_score or 0.0),
            "ai_adversarial_score": float(self.ai_adversarial_score or 0.0),
            "pq_fallback": bool(self.pq_fallback),
            "rules_triggered": self.rules_triggered,
            "explain_summary": self.explain_summary or "",
            "latency_ms": int(self.latency_ms or 0),
            "request": self.request,
            "response": self.response,
        }

    @classmethod
    def from_request_and_response(
        cls,
        tenant_id: str,
        source: str,
        event_id: str,
        event_type: str,
        threat_level: str,
        anomaly_score: float,
        ai_adversarial_score: float,
        pq_fallback: bool,
        rules_triggered: List[str],
        explain_summary: str,
        latency_ms: int,
        request_obj: Dict[str, Any],
        response_obj: Dict[str, Any],
    ) -> "DecisionRecord":
        return cls(
            tenant_id=tenant_id,
            source=source,
            event_id=event_id,
            event_type=event_type,
            threat_level=threat_level,
            anomaly_score=float(anomaly_score or 0.0),
            ai_adversarial_score=float(ai_adversarial_score or 0.0),
            pq_fallback=bool(pq_fallback),
            rules_triggered_json=json_dumps(list(rules_triggered or [])),
            explain_summary=explain_summary or "",
            latency_ms=int(latency_ms or 0),
            request_json=json_dumps(request_obj or {}),
            response_json=json_dumps(response_obj or {}),
        )
