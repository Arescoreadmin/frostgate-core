from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


class ROEPolicy(BaseModel):
    policy_id: str = "roe-default"
    max_disruption: int = 1
    ao_required_actions: list[str] = Field(default_factory=list)


class ROEEvaluationRequest(BaseModel):
    persona: Optional[str] = None
    classification: Optional[str] = None
    mitigations: list[dict] = Field(default_factory=list)


class ROEEvaluationResponse(BaseModel):
    gating_decision: str
    reason: str
    policy: ROEPolicy


class ROEEngine:
    def __init__(self, policy: Optional[ROEPolicy] = None) -> None:
        self.policy = policy or ROEPolicy()

    def evaluate(self, req: ROEEvaluationRequest) -> ROEEvaluationResponse:
        persona = (req.persona or "").strip().lower()
        classification = (req.classification or "").strip().upper()
        actions = [m.get("action") for m in req.mitigations if isinstance(m, dict)]

        if persona == "guardian" and classification == "SECRET" and "block_ip" in actions:
            return ROEEvaluationResponse(
                gating_decision="require_approval",
                reason="Guardian persona requires approval for disruptive actions.",
                policy=self.policy,
            )

        return ROEEvaluationResponse(
            gating_decision="allow",
            reason="No ROE constraints triggered.",
            policy=self.policy,
        )


router = APIRouter(prefix="/roe", tags=["roe"])


@router.get("/policy", response_model=ROEPolicy)
async def get_policy() -> ROEPolicy:
    return ROEPolicy()


@router.post("/evaluate", response_model=ROEEvaluationResponse)
async def evaluate_roe(req: ROEEvaluationRequest) -> ROEEvaluationResponse:
    engine = ROEEngine()
    return engine.evaluate(req)


def roe_engine_enabled() -> bool:
    return _env_bool("FG_ROE_ENGINE_ENABLED", False)
