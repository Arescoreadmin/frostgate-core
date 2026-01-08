from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ImpactEstimate(BaseModel):
    service_disruption_prob: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_affected_services: int = Field(default=0, ge=0)
    within_blast_radius_cap: bool = True
    model_version: Optional[str] = None
    details: Optional[dict[str, Any]] = None
