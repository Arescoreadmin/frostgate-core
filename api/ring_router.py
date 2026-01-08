from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


class ClassificationRing(str, Enum):
    UNCLASS = "UNCLASS"
    CUI = "CUI"
    SECRET = "SECRET"
    TOPSECRET = "TOPSECRET"


class RingPolicy(BaseModel):
    ring: ClassificationRing
    max_retention_days: int = 30
    encryption_required: bool = True
    model_isolation: bool = True
    cross_ring_queries_allowed: bool = False
    audit_level: str = Field(default="basic", pattern="^(basic|detailed|comprehensive)$")


class RingRouteRequest(BaseModel):
    classification: ClassificationRing


class RingRouteResponse(BaseModel):
    db_path: str
    model_path: str
    policy: RingPolicy


DEFAULT_POLICIES = {
    ClassificationRing.UNCLASS: RingPolicy(ring=ClassificationRing.UNCLASS, audit_level="basic"),
    ClassificationRing.CUI: RingPolicy(ring=ClassificationRing.CUI, audit_level="detailed"),
    ClassificationRing.SECRET: RingPolicy(ring=ClassificationRing.SECRET, audit_level="comprehensive"),
    ClassificationRing.TOPSECRET: RingPolicy(
        ring=ClassificationRing.TOPSECRET,
        audit_level="comprehensive",
        cross_ring_queries_allowed=False,
    ),
}


class RingRouter:
    def __init__(self, state_dir: str = "state", model_dir: str = "models") -> None:
        self.state_dir = state_dir
        self.model_dir = model_dir
        self.ring_policies = dict(DEFAULT_POLICIES)

    def route(self, classification: ClassificationRing) -> RingRouteResponse:
        policy = self.ring_policies[classification]
        model_path = f"{self.model_dir}/{classification.value.lower()}/ensemble.pkl"
        db_path = f"{self.state_dir}/{classification.value.lower()}/frostgate.db"
        return RingRouteResponse(db_path=db_path, model_path=model_path, policy=policy)

    def enforce_isolation(
        self,
        source_ring: ClassificationRing,
        target_ring: ClassificationRing,
    ) -> bool:
        if source_ring == target_ring:
            return True
        policy = self.ring_policies[source_ring]
        if not policy.cross_ring_queries_allowed:
            return False

        ring_order = [
            ClassificationRing.UNCLASS,
            ClassificationRing.CUI,
            ClassificationRing.SECRET,
            ClassificationRing.TOPSECRET,
        ]
        return ring_order.index(source_ring) >= ring_order.index(target_ring)


router = APIRouter(prefix="/rings", tags=["rings"])


@router.get("/policies", response_model=list[RingPolicy])
async def list_policies() -> list[RingPolicy]:
    return list(DEFAULT_POLICIES.values())


@router.post("/route", response_model=RingRouteResponse)
async def route_request(req: RingRouteRequest) -> RingRouteResponse:
    router_impl = RingRouter()
    return router_impl.route(req.classification)


@router.get("/isolation")
async def check_isolation(
    source: ClassificationRing,
    target: ClassificationRing,
) -> dict[str, bool]:
    router_impl = RingRouter()
    return {
        "allowed": router_impl.enforce_isolation(source, target),
    }


def ring_router_enabled() -> bool:
    return _env_bool("FG_RING_ROUTER_ENABLED", False)
