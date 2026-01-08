from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PolicyChangeRequest(BaseModel):
    change_id: str
    change_type: str
    proposed_by: str
    proposed_at: datetime
    justification: str
    rule_definition: Optional[dict] = None
    roe_update: Optional[dict] = None
    simulation_results: dict = Field(default_factory=dict)
    estimated_false_positives: int = 0
    estimated_true_positives: int = 0
    confidence: str = "medium"
    requires_approval_from: list[str] = Field(default_factory=list)
    approvals: list[dict] = Field(default_factory=list)
    status: str = "pending"
    deployed_at: Optional[datetime] = None


class PolicyChangeCreate(BaseModel):
    change_type: str
    proposed_by: str
    justification: str
    rule_definition: Optional[dict] = None
    roe_update: Optional[dict] = None


class PolicyApprovalRequest(BaseModel):
    approver: str
    notes: Optional[str] = None


_CHANGE_REQUESTS: dict[str, PolicyChangeRequest] = {}


router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/changes", response_model=list[PolicyChangeRequest])
async def list_changes() -> list[PolicyChangeRequest]:
    return list(_CHANGE_REQUESTS.values())


@router.post("/changes", response_model=PolicyChangeRequest)
async def create_change(req: PolicyChangeCreate) -> PolicyChangeRequest:
    change_id = f"pcr-{uuid.uuid4().hex[:8]}"
    change = PolicyChangeRequest(
        change_id=change_id,
        change_type=req.change_type,
        proposed_by=req.proposed_by,
        proposed_at=_utcnow(),
        justification=req.justification,
        rule_definition=req.rule_definition,
        roe_update=req.roe_update,
        simulation_results={},
        estimated_false_positives=0,
        estimated_true_positives=0,
        confidence="medium",
        requires_approval_from=["security-lead", "ciso"],
        approvals=[],
        status="pending",
    )
    _CHANGE_REQUESTS[change_id] = change
    return change


@router.post("/changes/{change_id}/approve", response_model=PolicyChangeRequest)
async def approve_change(change_id: str, req: PolicyApprovalRequest) -> PolicyChangeRequest:
    change = _CHANGE_REQUESTS.get(change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Change request not found")

    change.approvals.append({
        "approver": req.approver,
        "approved_at": _utcnow(),
        "notes": req.notes,
    })

    if len(change.approvals) >= len(change.requires_approval_from):
        change.status = "deployed"
        change.deployed_at = _utcnow()
    else:
        change.status = "pending"

    _CHANGE_REQUESTS[change_id] = change
    return change


def governance_enabled() -> bool:
    return _env_bool("FG_GOVERNANCE_ENABLED", False)
