# api/ingest.py
from __future__ import annotations

import json
from api.decision_diff import (
    compute_decision_diff,
    snapshot_from_current,
    snapshot_from_record,
)
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from api.auth_scopes import require_scopes
from api.db import get_db
from api.db_models import DecisionRecord
from api.ingest_schemas import IngestResponse
from api.schemas import TelemetryInput
from engine.evaluate import evaluate

log = logging.getLogger("frostgate.ingest")

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---- rate limit guard: keep stable, do not invent new paths mid-MVP ----
try:
    from api.ratelimit import rate_limit_guard  # your known path earlier

    _RATE_LIMIT_DEP = Depends(rate_limit_guard)
except Exception:  # pragma: no cover

    async def _noop():
        return None

    _RATE_LIMIT_DEP = Depends(_noop)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoz(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"_unserializable": str(obj)}, separators=(",", ":"))


def _resolve_tenant_id(
    req: TelemetryInput, x_tenant_id: Optional[str], request: Request
) -> str:
    tid = (x_tenant_id or req.tenant_id or "").strip()
    if not tid:
        tid = getattr(request.state, "tenant_id", "") or "unknown"
    request.state.tenant_id = tid
    return tid


def _resolve_source(req: TelemetryInput) -> str:
    src = (req.source or "").strip()
    return src or "agent"


def _extract_event_id(req: TelemetryInput) -> str:
    eid = (getattr(req, "event_id", None) or "").strip()
    return eid or str(uuid.uuid4())


def _extract_event_type(req: TelemetryInput) -> str:
    et = (req.event_type or "").strip()
    return et or "unknown"


def _extract_actor_target(
    payload: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    actor = (
        payload.get("actor")
        or payload.get("username")
        or payload.get("user")
        or payload.get("principal")
    )
    target = (
        payload.get("target")
        or payload.get("resource")
        or payload.get("dst")
        or payload.get("dst_ip")
    )
    return (
        str(actor) if actor is not None else None,
        str(target) if target is not None else None,
    )


def _extract_src_ip(payload: dict[str, Any]) -> Optional[str]:
    src_ip = payload.get("src_ip") or payload.get("source_ip") or payload.get("ip")
    return str(src_ip) if src_ip is not None else None


@router.post(
    "",
    response_model=IngestResponse,
    dependencies=[
        Depends(require_scopes("ingest:write")),
        _RATE_LIMIT_DEP,
    ],
)
async def ingest(
    req: TelemetryInput,
    request: Request,
    db: Session = Depends(get_db),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
) -> IngestResponse:
    """
    Ingest telemetry, evaluate, persist.
    This endpoint should not hard-crash on evaluation/persistence errors.
    """
    t0 = time.time()
    ts = _utcnow()

    tenant_id = _resolve_tenant_id(req, x_tenant_id, request)
    source = _resolve_source(req)

    event_id = _extract_event_id(req)
    event_type = _extract_event_type(req)

    payload: dict[str, Any] = req.payload or {}
    actor, target = _extract_actor_target(payload)
    src_ip = _extract_src_ip(payload)

    canonical_request: dict[str, Any] = {
        "tenant_id": tenant_id,
        "source": source,
        "timestamp": _isoz(ts),
        "event_id": event_id,
        "event_type": event_type,
        "src_ip": src_ip,
        "actor": actor,
        "target": target,
        "payload": payload,
        "meta": getattr(req, "meta", None),
        "classification": getattr(req, "classification", None),
        "persona": getattr(req, "persona", None),
    }

    # ---- evaluate (never crash ingest) ----
    try:
        decision = (
            evaluate(
                {
                    "tenant_id": tenant_id,
                    "source": source,
                    "event_type": event_type,
                    "payload": payload,
                }
            )
            or {}
        )
    except Exception:
        log.exception("evaluation failed")
        decision = {
            "tenant_id": tenant_id,
            "source": source,
            "event_type": event_type,
            "threat_level": "low",
            "mitigations": [],
            "rules": ["rule:evaluate_exception"],
            "anomaly_score": 0.0,
            "ai_adversarial_score": 0.0,
            "summary": "evaluation error; defaulted to low threat",
        }

    threat_level = str(decision.get("threat_level") or "low").lower()
    latency_ms = int((time.time() - t0) * 1000)

    resp = IngestResponse(
        status="ok",
        event_id=event_id,
        tenant_id=tenant_id,
        source=source,
        event_type=event_type,
        decision=decision,
        threat_level=threat_level,
        latency_ms=latency_ms,
        persisted=True,
    )

    # ---- persist (best effort) ----
    try:
        rules = decision.get("rules_triggered") or decision.get("rules") or []
        summary = decision.get("summary") or ""
        # --- Decision Diff (compute + persist) ---
        try:
            prev = (
                db.query(DecisionRecord)
                .filter(
                    DecisionRecord.tenant_id == tenant_id,
                    DecisionRecord.source == source,
                    DecisionRecord.event_type == event_type,
                )
                .order_by(DecisionRecord.id.desc())
                .first()
            )
            prev_snapshot = snapshot_from_record(prev) if prev is not None else None
            curr_snapshot = snapshot_from_current(
                threat_level=threat_level,
                rules_triggered=rules,
                score=decision.get("score"),
            )
            decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)
        except Exception:
            decision_diff_obj = None
        # --- end Decision Diff ---

        rec = DecisionRecord(
            tenant_id=tenant_id,
            source=source,
            event_id=event_id,
            event_type=event_type,
            threat_level=threat_level,
            anomaly_score=float(decision.get("anomaly_score") or 0.0),
            ai_adversarial_score=float(decision.get("ai_adversarial_score") or 0.0),
            pq_fallback=bool(decision.get("pq_fallback") or False),
            latency_ms=latency_ms,
            # IMPORTANT: these field names MUST match your model/table
            request_json=_safe_json(canonical_request),
            response_json=_safe_json(resp.model_dump()),
            rules_triggered_json=_safe_json(rules),
            explain_summary=str(summary),
            decision_diff_json=decision_diff_obj,
        )
        db.add(rec)
        db.commit()
    except Exception:
        resp.persisted = False
        log.exception("failed to persist decision")
        try:
            db.rollback()
        except Exception:
            pass

    return resp
