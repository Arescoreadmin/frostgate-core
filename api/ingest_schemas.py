from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class IngestResponse(BaseModel):
    status: str = "ok"
    event_id: str
    tenant_id: str
    source: str
    event_type: str
    decision: Dict[str, Any] = Field(default_factory=dict)

    # Optional convenience fields (for UI / search / filtering)
    threat_level: Optional[str] = None
    latency_ms: Optional[int] = None
