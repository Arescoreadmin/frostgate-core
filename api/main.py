from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI, Request, Header
from loguru import logger

from .schemas import TelemetryInput, DefendResponse, ExplainBlock
from .config import settings
from engine import evaluate_rules

app = FastAPI(
    title="FrostGate Core API",
    version="0.2.0",
    description="MVP defense API for FrostGate Core `/defend` with a basic rules engine.",
)


@app.on_event("startup")
async def startup_event():
    logger.info("FrostGate Core API starting in env={}", settings.env)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "env": settings.env}


@app.get("/status")
async def status() -> Dict[str, Any]:
    return {
        "service": "frostgate-core",
        "version": "0.2.0",
        "env": settings.env,
        "components": {
            "ensemble": "rules-only-mvp",
            "merkle_anchor": "pending",
            "supervisor": "pending",
        },
    }


@app.post("/defend", response_model=DefendResponse)
async def defend(
    telemetry: TelemetryInput,
    request: Request,
    x_pq_fallback: str | None = Header(default=None, alias=settings.pq_fallback_header),
):
    """
    `/defend` MVP backed by a simple rules engine in engine.rules.
    """
    now = datetime.now(timezone.utc)

    # clock drift vs event timestamp
    try:
        ts = datetime.fromisoformat(telemetry.timestamp.replace("Z", "+00:00"))
        clock_drift_ms = int((now - ts).total_seconds() * 1000)
    except Exception:
        clock_drift_ms = 0

    (
        threat_level,
        mitigations,
        rules_triggered,
        anomaly_score,
        ai_adv_score,
    ) = evaluate_rules(telemetry)

    pq_fallback = bool(x_pq_fallback)

    explain = ExplainBlock(
        summary=f"MVP decision for tenant={telemetry.tenant_id}, source={telemetry.source}",
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        llm_note="MVP stub – rules only, no real LLM yet.",
    )

    resp = DefendResponse(
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=ai_adv_score,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )

    logger.info(
        "defend decision",
        extra={
            "tenant_id": telemetry.tenant_id,
            "source": telemetry.source,
            "threat_level": threat_level,
            "rules": rules_triggered,
        },
    )
    return resp


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
