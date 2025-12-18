from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth_scopes import verify_api_key, require_scope
from api.ratelimit import rate_limit_guard


router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_scope("ingest:write")),
        Depends(rate_limit_guard),
    ],
)

class TelemetryInput(BaseModel):
    source: str = Field(...)
    tenant_id: str = Field(...)
    timestamp: datetime = Field(...)
    payload: dict[str, Any] = Field(default_factory=dict)

class IngestResponse(BaseModel):
    accepted: bool
    event_id: str
    received_at: datetime

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def _event_id(req: TelemetryInput) -> str:
    ts = _to_utc(req.timestamp).isoformat().replace("+00:00", "Z")
    raw = f"{req.tenant_id}|{req.source}|{ts}|{_canonical_json(req.payload or {})}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

@router.post("", response_model=IngestResponse)
async def ingest(request: TelemetryInput) -> IngestResponse:
    # MVP: accept + return deterministic id. Next: disk queue or DB spool.
    return IngestResponse(
        accepted=True,
        event_id=_event_id(request),
        received_at=datetime.now(timezone.utc),
    )
