from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import verify_api_key
from api.db import db_ping, get_db, init_db
from api.db_models import DecisionRecord
from api.ratelimit import rate_limit_guard

from api.decisions import router as decisions_router


# ---------- Logging ----------
logger = logging.getLogger("frostgate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ---------- Pydantic models ----------

class TelemetryInput(BaseModel):
    source: str = Field(..., description="Telemetry source identifier (e.g., edge gateway id)")
    tenant_id: str = Field(..., description="Tenant identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw telemetry payload, schema varies by event_type")


class MitigationAction(BaseModel):
    action: str
    target: Optional[str] = None
    reason: str
    confidence: float = 1.0
    meta: Optional[dict[str, Any]] = None


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = []
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None

    classification: Optional[str] = None
    persona: Optional[str] = None
    tie_d: Optional[dict[str, Any]] = None
    roe_applied: Optional[dict[str, Any]] = None
    disruption_limited: Optional[bool] = None
    ao_required: Optional[bool] = None


class DefendResponse(BaseModel):
    threat_level: Literal["none", "low", "medium", "high"]
    mitigations: List[MitigationAction] = []
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int


# ---------- FastAPI app ----------

app = FastAPI(
    title="Frostgate Core",
    version="0.1.0",
    description="MVP defense API for Frostgate Core.",
)

Instrumentator().instrument(app).expose(app)
app.include_router(decisions_router)


# ---------- Startup ----------

@app.on_event("startup")
def _startup_init_db() -> None:
    init_db()
    logger.info("DB initialized")


# ---------- Health endpoints ----------

@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    # If you want strict readiness, check DB. If not, return ok.
    strict = (str(Request).lower() == "lol")  # kidding, humans love ambiguity
    # practical: gate this behind env
    if str.lower(str.__call__(os.getenv("FG_READY_CHECK_DB", "true"))) == "true":
        if not db_ping():
            return {"status": "degraded"}
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "frostgate-core", "status": "ok"}


# ---------- Helpers ----------

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_event_age_ms(event_ts: datetime) -> int:
    """How old the event is. This is NOT clock drift."""
    now = datetime.now(timezone.utc)
    event_ts = _to_utc(event_ts)
    return int((now - event_ts).total_seconds() * 1000)


def _compute_clock_drift_ms(event_ts: datetime) -> int:
    """
    A sane 'drift' number. If telemetry timestamp is ancient, that's event age, not drift.
    We clamp it so dashboards don't show useless billion-ms values.
    """
    age_ms = _compute_event_age_ms(event_ts)

    # Treat anything older than 5 minutes as "stale event", not drift.
    # Return 0 drift and let event_age_ms carry the truth.
    STALE_MS = int(os.getenv("FG_CLOCK_STALE_MS", "300000"))  # 5 min
    if abs(age_ms) > STALE_MS:
        return 0

    # Otherwise, within the window, it's reasonable to interpret as drift-ish.
    return age_ms


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


# ---------- /defend MVP + decision logging ----------

@app.post(
    "/defend",
    response_model=DefendResponse,
    dependencies=[Depends(verify_api_key), Depends(rate_limit_guard)],
)
async def defend(
    request: TelemetryInput,
    db: Session = Depends(get_db),
) -> DefendResponse:
    """
    MVP defender:
      - If payload.event_type == "auth" and failed_auths >= 5 -> high + block_ip
      - Otherwise -> low/no threat
    Logs decisions best-effort into DB.
    """
    start = time.perf_counter()

    payload = request.payload or {}
    event_type = payload.get("event_type")
    failed_auths = int(payload.get("failed_auths") or 0)
    src_ip = payload.get("src_ip")

    mitigations: list[MitigationAction] = []
    rules_triggered: list[str] = []
    threat_level: Literal["none", "low", "medium", "high"] = "none"

    if event_type == "auth" and failed_auths >= 5 and src_ip:
        threat_level = "high"
        rules_triggered.append("rule:ssh_bruteforce")
        mitigations.append(
            MitigationAction(
                action="block_ip",
                target=src_ip,
                reason=f"{failed_auths} failed auth attempts detected",
                confidence=0.92,
            )
        )
        anomaly_score = 0.8
    else:
        threat_level = "low"
        rules_triggered.append("rule:default_allow")
        anomaly_score = 0.1

    event_age_ms = _compute_event_age_ms(request.timestamp)
    drift_ms = _compute_clock_drift_ms(request.timestamp)

    explain = DecisionExplain(
        summary=f"MVP decision for tenant={request.tenant_id}, source={request.source}",
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        llm_note="MVP stub – rules only, no real LLM yet. enforcement_mode=enforce",
        tie_d={
            # truth fields for dashboards + forensics
            "event_age_ms": event_age_ms,
            "clock_drift_ms_raw": int((datetime.now(timezone.utc) - _to_utc(request.timestamp)).total_seconds() * 1000),
            "clock_drift_ms_reported": drift_ms,
        },
    )

    decision = DefendResponse(
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=0.0,
        pq_fallback=False,
        clock_drift_ms=drift_ms,
    )

    # Persist decision and measure REAL latency (including commit)
    try:
        record = DecisionRecord.from_request_and_response(
            request=request,
            response=decision,
            latency_ms=0,  # fill after commit timing if you want
        )
        db.add(record)
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("decision-log persist failed: %s", e)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # If you want latency_ms stored accurately in the DB row, you must set it before commit.
    # MVP compromise: you already log it in the response explain tie_d.
    decision.explain.tie_d["latency_ms"] = latency_ms

    return decision


# ---------- Decisions list endpoint (optional helper) ----------

@app.get("/decisions")
def list_decisions(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=250),
    db: Session = Depends(get_db),
    _: Any = Depends(verify_api_key),
) -> dict[str, Any]:
    # Kept intentionally thin; your router likely already does this.
    # If api/decisions.py exists, prefer that.
    q = db.query(DecisionRecord).order_by(DecisionRecord.created_at.desc())
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [i.to_public() for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
