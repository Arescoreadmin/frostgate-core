# api/db_models.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from sqlalchemy import Column, String

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

prev_hash = Column(String(64), nullable=True)
chain_hash = Column(String(64), nullable=True)


def utcnow():
    return datetime.now(timezone.utc)


def hash_api_key(api_key: str) -> str:
    # Stable hashing for lookup. (If you later want pepper/salt, do it carefully.)
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, default="default")
    prefix = Column(String(64), nullable=False, index=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    scopes_csv = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)

    # Must be NOT NULL and must default for SQLite + ORM
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )

    tenant_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    event_type = Column(String, nullable=True)

    threat_level = Column(String, nullable=True)
    anomaly_score = Column(Float, nullable=True)
    ai_adversarial_score = Column(Float, nullable=True)
    pq_fallback = Column(Boolean, nullable=True)

    rules_triggered_json = Column(Text, nullable=True)
    explain_summary = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    request_json = Column(Text, nullable=True)
    response_json = Column(Text, nullable=True)
