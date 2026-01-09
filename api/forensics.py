from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.db import get_db
from api.db_models import DecisionRecord


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def _decision_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _maybe_load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


router = APIRouter(prefix="/forensics", tags=["forensics"])


@router.get("/snapshot/{event_id}")
async def snapshot(event_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = (
        db.query(DecisionRecord).filter(DecisionRecord.event_id == event_id).first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    request_payload = _maybe_load_json(getattr(record, "request_json", None))
    response_payload = _maybe_load_json(getattr(record, "response_json", None))

    snapshot_payload = {
        "event_id": event_id,
        "created_at": record.created_at.isoformat()
        if getattr(record, "created_at", None)
        else None,
        "request": request_payload,
        "response": response_payload,
        "threat_level": getattr(record, "threat_level", None),
    }

    return {
        "snapshot_id": f"snap-{event_id[:12]}",
        "timestamp": _utcnow().isoformat(),
        "snapshot": snapshot_payload,
        "decision_hash": _decision_hash(snapshot_payload),
    }


@router.get("/audit_trail/{event_id}")
async def audit_trail(event_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = (
        db.query(DecisionRecord).filter(DecisionRecord.event_id == event_id).first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    chain_hash = getattr(record, "chain_hash", None)
    prev_hash = getattr(record, "prev_hash", None)

    return {
        "event_id": event_id,
        "timeline": [
            {
                "timestamp": record.created_at.isoformat()
                if getattr(record, "created_at", None)
                else None,
                "summary": "Decision recorded",
            }
        ],
        "reproducible": bool(chain_hash is not None or prev_hash is not None),
        "chain_hash": chain_hash,
        "prev_hash": prev_hash,
    }


def forensics_enabled() -> bool:
    return _env_bool("FG_FORENSICS_ENABLED", False)
