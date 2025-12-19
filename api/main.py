# api/main.py
from __future__ import annotations

import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from fastapi import Depends, FastAPI, Query, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth_scopes import verify_api_key
from api.db_models import DecisionRecord
from api.ratelimit import rate_limit_guard
from api.decisions import router as decisions_router


logger = logging.getLogger("frostgate")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ----------------------------
# DB imports (resilient)
# ----------------------------
try:
    # Preferred: api.db exports db_ping
    from api.db import db_ping, get_db, init_db
except ImportError:
    # Fallback: api.db doesn't export db_ping yet
    from api.db import get_db, init_db

    def db_ping() -> bool:
        """
        Fallback DB readiness probe if api.db doesn't export db_ping.
        Uses get_db() and runs SELECT 1.
        """
        try:
            for db in get_db():
                db.execute(text("SELECT 1"))
                return True
        except Exception:
            logger.exception("db_ping fallback failed")
            return False
        return False


# ----------------------------
# Pydantic models
# ----------------------------

class TelemetryInput(BaseModel):
    tenant_id: str = Field(..., description="Tenant identifier")
    source: str = Field(..., description="Telemetry source identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")
    event_type: str = Field(..., description="Event type (e.g., auth.bruteforce)")
    event: Dict[str, Any] = Field(default_factory=dict, description="Event payload")


class MitigationAction(BaseModel):
    action: Literal[
        "block_ip",
        "step_up_auth",
        "require_captcha",
        "throttle",
        "quarantine",
        "notify",
        "none",
    ]
    target: str
    reason: str
    confidence: float = 0.5


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = Field(default_factory=list)
    anomaly_score: float = 0.0
    llm_note: str = ""
    tie_d: Dict[str, Any] = Field(default_factory=dict)


class DefendResponse(BaseModel):
    event_id: str
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: List[MitigationAction] = Field(default_factory=list)
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int = 0


# ----------------------------
# App + instrumentation
# ----------------------------

app = FastAPI(
    title="Frostgate Core",
    version="0.1.0",
    description="MVP defense API for Frostgate Core.",
)

from api.decisions import router as decisions_router
from api.ingest import router as ingest_router

app.include_router(decisions_router)
app.include_router(ingest_router)

Instrumentator().instrument(app).expose(app)
app.include_router(decisions_router)


# ----------------------------
# Startup
# ----------------------------

@app.on_event("startup")
def _startup_init_db() -> None:
    init_db()
    logger.info("Frostgate Core DB initialized")


# ----------------------------
# Health endpoints
# ----------------------------

@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict[str, str]:
    # Backwards-compatible endpoint for monitors/tests
    return {
        "status": "ok",
        "env": os.getenv("FG_ENV", "dev"),
        "service": "frostgate-core",
        "version": os.getenv("FG_VERSION", "0.1.0"),
    }


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    if os.getenv("FG_READY_CHECK_DB", "true").strip().lower() == "true":
        if not db_ping():
            return {"status": "degraded"}
    return {"status": "ready"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "frostgate-core", "status": "ok"}


# ----------------------------
# Helpers (time + drift)
# ----------------------------

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_event_age_ms(event_ts: datetime) -> int:
    now = datetime.now(timezone.utc)
    event_ts = _to_utc(event_ts)
    return int((now - event_ts).total_seconds() * 1000)


def _compute_clock_drift_ms(event_ts: datetime) -> int:
    """
    Avoid useless gigantic drift for stale events.
    If event older than STALE_MS, report drift=0 and rely on event_age_ms.
    """
    age_ms = _compute_event_age_ms(event_ts)
    stale_ms = int(os.getenv("FG_CLOCK_STALE_MS", "300000"))  # 5 min default
    if abs(age_ms) > stale_ms:
        return 0
    return age_ms


# ----------------------------
# Ensemble heuristics
# ----------------------------

def _flatten_values_for_scan(obj: Any) -> Iterable[str]:
    if obj is None:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_values_for_scan(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _flatten_values_for_scan(v)
    else:
        yield str(obj)


def _detect_ai_adversarial(event: dict[str, Any]) -> float:
    """
    Guardrail heuristic: detect prompt-injection-ish or destructive operator strings.
    This is not “AI”, it’s a cheap smoke alarm. Cheap smoke alarms still save houses.
    """
    suspicious_markers = [
        "ignore previous",
        "prompt injection",
        "system prompt",
        "exfil",
        "base64",
        "rm -rf",
        "curl http",
        "powershell -enc",
        "sudo ",
        "sshpass",
        "shadow file",
        "token",
        "api key",
    ]
    score = 0.0
    for text_val in _flatten_values_for_scan(event):
        t = text_val.lower()
        for marker in suspicious_markers:
            if marker in t:
                score += 0.15
    return float(max(0.0, min(score, 1.0)))


def _estimate_anomaly_score(event_type: str, event: dict[str, Any]) -> float:
    """
    Lightweight bounded anomaly heuristic [0..1].
    Uses common fields if present; otherwise stays low.
    """
    score = 0.05

    attempts = int(event.get("attempts") or event.get("failed_auths") or 0)
    score += min(attempts * 0.05, 0.5)

    if event.get("new_device") is True:
        score += 0.15

    if event.get("impossible_travel") is True:
        score += 0.2

    if "geo_distance_km" in event:
        try:
            km = float(event.get("geo_distance_km") or 0.0)
            score += min(math.log1p(max(km, 0.0)) / 10.0, 0.25)
        except Exception:
            score += 0.05

    # If someone provides a 0..1 reputation where 1 is good, invert into risk contribution.
    rep = event.get("network_reputation")
    if isinstance(rep, (int, float)):
        score += max(0.0, (1.0 - min(float(rep), 1.0))) * 0.2

    # Slight bump for obviously sensitive event types
    if event_type.startswith("auth.") or event_type.startswith("iam."):
        score += 0.05

    return float(max(0.0, min(score, 1.0)))


def _evaluate_rules(event_type: str, event: dict[str, Any]) -> Tuple[
    Literal["none", "low", "medium", "high"],
    List[str],
    List[MitigationAction],
]:
    """
    Rule-based detections. High-signal conditions.
    """
    rules: List[str] = []
    mitigations: List[MitigationAction] = []
    threat: Literal["none", "low", "medium", "high"] = "none"

    src_ip = event.get("ip") or event.get("src_ip")
    user = event.get("username") or event.get("user") or "user:unknown"
    attempts = int(event.get("attempts") or event.get("failed_auths") or 0)

    # Auth brute force (your actual event_type example: auth.bruteforce)
    if event_type.startswith("auth") and attempts >= 5 and src_ip:
        threat = "high"
        rules.append("rule:auth_bruteforce")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip),
                reason=f"{attempts} auth failures detected",
                confidence=0.92,
            )
        )

    # Optional flags (if your telemetry sets them)
    if event.get("impossible_travel") is True:
        if threat != "high":
            threat = "medium"
        rules.append("rule:impossible_travel")
        mitigations.append(
            MitigationAction(
                action="step_up_auth",
                target=str(user),
                reason="impossible travel detected",
                confidence=0.74,
            )
        )

    if event.get("honeypot_touch") is True:
        threat = "high"
        rules.append("rule:honeypot_access")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip or "network:unknown"),
                reason="honeypot resource accessed",
                confidence=0.9,
            )
        )

    if not rules:
        rules.append("rule:default_allow")
        threat = "low"

    return threat, rules, mitigations


# Cache parsed JSON for /defend so limiter can key on tenant_id without re-reading the body
@app.middleware("http")
async def _capture_defend_body(request: Request, call_next):
    if request.url.path == "/defend" and request.method.upper() == "POST":
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            try:
                request.state.telemetry_body = await request.json()
            except Exception:
                request.state.telemetry_body = None
    return await call_next(request)


# ----------------------------
# /defend (ensemble + decision logging)
# ----------------------------

@app.post(
    "/defend",
    response_model=DefendResponse,
    dependencies=[Depends(verify_api_key), Depends(rate_limit_guard)],
)
async def defend(
    request: TelemetryInput,
    db: Session = Depends(get_db),
) -> DefendResponse:
    start = time.perf_counter()

    event_id = str(uuid.uuid4())
    event_type = (request.event_type or "").strip() or "unknown"
    event = request.event or {}

    # 1) Rule engine
    rule_threat, rules_triggered, mitigations = _evaluate_rules(event_type, event)

    # 2) Heuristics
    anomaly_score = _estimate_anomaly_score(event_type, event)
    ai_adversarial_score = _detect_ai_adversarial(event)

    # 3) Ensemble escalation
    threat_level: Literal["none", "low", "medium", "high"] = rule_threat
    if threat_level == "none":
        threat_level = "low"

    max_signal = max(anomaly_score, ai_adversarial_score)
    if max_signal >= 0.75:
        threat_level = "high"
    elif max_signal >= 0.40 and threat_level == "low":
        threat_level = "medium"

    # 4) Decorate explain + add guardrail-derived mitigation
    src_ip = event.get("ip") or event.get("src_ip")
    user = event.get("username") or event.get("user")

    if ai_adversarial_score >= 0.45:
        rules_triggered.append("guardrail:ai_adversarial")

    if anomaly_score >= 0.40 and "anomaly:behavioral" not in rules_triggered:
        rules_triggered.append("anomaly:behavioral")

    if ai_adversarial_score >= 0.75 and src_ip:
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=str(src_ip),
                reason="AI-adversarial payload patterns detected",
                confidence=0.70,
            )
        )
    elif ai_adversarial_score >= 0.45 and user:
        mitigations.append(
            MitigationAction(
                action="require_captcha",
                target=str(user),
                reason="Potential prompt-injection / automation abuse detected",
                confidence=0.55,
            )
        )
    elif threat_level in ("medium", "high") and src_ip:
        mitigations.append(
            MitigationAction(
                action="throttle",
                target=str(src_ip),
                reason="Elevated threat: apply throttling to reduce blast radius",
                confidence=0.55,
            )
        )

    event_age_ms = _compute_event_age_ms(request.timestamp)
    drift_ms = _compute_clock_drift_ms(request.timestamp)

    llm_note_parts = [
        f"rules={','.join(rules_triggered)}",
        f"anomaly={anomaly_score:.2f}",
        f"ai_adv={ai_adversarial_score:.2f}",
        "enforcement_mode=enforce",
    ]

    explain = DecisionExplain(
        summary=f"Defense decision for tenant={request.tenant_id}, source={request.source}",
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        llm_note="; ".join(llm_note_parts),
        tie_d={
            "event_age_ms": event_age_ms,
            "clock_drift_ms_raw": int(
                (datetime.now(timezone.utc) - _to_utc(request.timestamp)).total_seconds() * 1000
            ),
            "clock_drift_ms_reported": drift_ms,
        },
    )

    decision = DefendResponse(
        event_id=event_id,
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=ai_adversarial_score,
        pq_fallback=False,
        clock_drift_ms=drift_ms,
    )

    latency_ms = int((time.perf_counter() - start) * 1000)
    decision.explain.tie_d["latency_ms"] = latency_ms

    # Persist decision best-effort
    try:
        record = DecisionRecord.from_request_and_response(
            tenant_id=request.tenant_id,
            source=request.source,
            event_id=event_id,
            event_type=event_type,
            threat_level=threat_level,
            anomaly_score=anomaly_score,
            ai_adversarial_score=ai_adversarial_score,
            pq_fallback=False,
            rules_triggered=rules_triggered,
            explain_summary=explain.summary,
            latency_ms=latency_ms,
            request_obj=request.model_dump(),
            response_obj=decision.model_dump(),
        )
        db.add(record)
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("decision-log persist failed: %s", e)

    return decision


# ----------------------------
# /decisions passthrough (optional)
# ----------------------------

def _sa_row_to_dict(row: Any) -> dict[str, Any]:
    """
    Safe serializer: only SQLAlchemy table columns, no _sa_instance_state.
    """
    try:
        cols = row.__table__.columns  # type: ignore[attr-defined]
        return {c.name: getattr(row, c.name) for c in cols}
    except Exception:
        # Worst-case fallback: return something not totally useless
        return {"id": getattr(row, "id", None)}


@app.get("/decisions")
async def decisions_passthrough(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=200),
):
    """
    Backwards-compatible passthrough if something expects /decisions directly
    (your main router already includes api.decisions; keep this only if needed).
    """
    rows = (
        db.query(DecisionRecord)
        .order_by(DecisionRecord.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": [_sa_row_to_dict(r) for r in rows]}
