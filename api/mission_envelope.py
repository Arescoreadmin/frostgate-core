from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


class MissionEnvelope(BaseModel):
    mission_id: str
    classification_level: str
    risk_tier: Optional[str] = None
    allowed_mitigations: list[str] = Field(default_factory=list)
    budget_cap: Optional[int] = None
    blast_radius_cap: Optional[int] = None
    persona: Optional[str] = None
    forensic_threshold: Optional[float] = None
    model_version: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    scope_hash: Optional[str] = None
    signature: Optional[str] = None

    def is_active(self, now: Optional[datetime] = None) -> bool:
        if not self.valid_from and not self.valid_to:
            return True
        now = now or _utcnow()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_to and now > self.valid_to:
            return False
        return True


DEFAULT_ENVELOPES = [
    MissionEnvelope(
        mission_id="fgc-mission-001",
        classification_level="CUI",
        risk_tier="tier-1",
        allowed_mitigations=["block_ip"],
        budget_cap=100,
        blast_radius_cap=5,
        persona="Sentinel",
        forensic_threshold=0.95,
        model_version="mvp",
        valid_from=_utcnow(),
    )
]


def _load_envelopes() -> list[MissionEnvelope]:
    path = (os.getenv("FG_MISSION_ENVELOPE_PATH") or "").strip()
    if not path:
        return list(DEFAULT_ENVELOPES)

    if not os.path.exists(path):
        return list(DEFAULT_ENVELOPES)

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    envelopes: list[MissionEnvelope] = []
    for item in payload or []:
        envelopes.append(MissionEnvelope.model_validate(item))
    return envelopes


router = APIRouter(prefix="/missions", tags=["missions"])


@router.get("", response_model=list[MissionEnvelope])
async def list_missions() -> list[MissionEnvelope]:
    """Return mission envelopes loaded from disk or defaults."""
    return _load_envelopes()


@router.get("/{mission_id}", response_model=MissionEnvelope)
async def get_mission(mission_id: str) -> MissionEnvelope:
    """Fetch a single mission envelope by id."""
    for envelope in _load_envelopes():
        if envelope.mission_id == mission_id:
            return envelope
    raise HTTPException(status_code=404, detail="Mission envelope not found")


@router.get("/{mission_id}/status")
async def mission_status(mission_id: str) -> dict[str, str]:
    """Return basic status flags for the mission envelope."""
    for envelope in _load_envelopes():
        if envelope.mission_id == mission_id:
            return {
                "mission_id": envelope.mission_id,
                "active": str(envelope.is_active()).lower(),
                "classification_level": envelope.classification_level,
            }
    raise HTTPException(status_code=404, detail="Mission envelope not found")


def mission_envelopes_enabled() -> bool:
    return _env_bool("FG_MISSION_ENVELOPE_ENABLED", False)
