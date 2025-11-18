from datetime import datetime, timezone
from typing import Dict, Any, List

from fastapi import FastAPI, Request, Header, Query
from loguru import logger

from .schemas import TelemetryInput, DefendResponse, ExplainBlock, MitigationAction
from .config import settings
from .logging_config import configure_logging
from engine import evaluate_rules, record_decision, list_decisions
from tools.telemetry.loader import load_golden_samples

# Configure logging before app starts
configure_logging()

app = FastAPI(
    title="FrostGate Core API",
    version="0.5.0",
    description=(
        "FrostGate Core MVP: rules engine, golden-sample tester, "
        "structured logging, decision history, and enforcement modes."
    ),
)


@app.on_event("startup")
async def startup_event():
    logger.info(
        "service_start",
        extra={
            "env": settings.env,
            "service": "frostgate-core",
            "enforcement_mode": settings.enforcement_mode,
        },
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    logger.debug("health_check")
    return {"status": "ok", "env": settings.env, "enforcement_mode": settings.enforcement_mode}


@app.get("/status")
async def status() -> Dict[str, Any]:
    return {
        "service": "frostgate-core",
        "version": "0.5.0",
        "env": settings.env,
        "enforcement_mode": settings.enforcement_mode,
        "components": {
            "ensemble": "rules-only-mvp",
            "merkle_anchor": "pending",
            "supervisor": "pending",
        },
    }


def _apply_enforcement_mode(
    mitigations: List[MitigationAction],
) -> List[MitigationAction]:
    """
    If enforcement_mode=observe, convert all mitigations to log_only.
    """
    if settings.enforcement_mode != "observe":
        return mitigations

    transformed: List[MitigationAction] = []
    for m in mitigations:
        transformed.append(
            MitigationAction(
                action="log_only",
                target=m.target,
                reason=f"[observe-only] {m.reason}",
                confidence=m.confidence,
            )
        )
    return transformed


@app.post("/defend", response_model=DefendResponse)
async def defend(
    telemetry: TelemetryInput,
    request: Request,
    x_pq_fallback: str | None = Header(default=None, alias=settings.pq_fallback_header),
):
    """
    Primary `/defend` endpoint backed by the rules engine.
    Also records decisions into in-memory history.
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

    # Apply enforcement mode transformation
    effective_mitigations = _apply_enforcement_mode(mitigations)

    explain = ExplainBlock(
        summary=f"MVP decision for tenant={telemetry.tenant_id}, source={telemetry.source}",
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        llm_note=(
            "MVP stub – rules only, no real LLM yet. "
            f"enforcement_mode={settings.enforcement_mode}"
        ),
    )

    resp = DefendResponse(
        threat_level=threat_level,
        mitigations=effective_mitigations,
        explain=explain,
        ai_adversarial_score=ai_adv_score,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )

    # Record into history (with original threat, but effective mitigations mode)
    record_decision(
        tenant_id=telemetry.tenant_id,
        source=telemetry.source,
        threat_level=threat_level,
        rules_triggered=rules_triggered,
        anomaly_score=anomaly_score,
        ai_adv_score=ai_adv_score,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )

    logger.info(
        "defend_decision",
        extra={
            "tenant_id": telemetry.tenant_id,
            "source": telemetry.source,
            "threat_level": threat_level,
            "rules": rules_triggered,
            "anomaly_score": anomaly_score,
            "ai_adv_score": ai_adv_score,
            "pq_fallback": pq_fallback,
            "clock_drift_ms": clock_drift_ms,
            "enforcement_mode": settings.enforcement_mode,
            "mitigation_count": len(effective_mitigations),
        },
    )
    return resp


@app.get("/defend/test")
async def defend_test(
    limit: int = Query(10, ge=1, le=100),
    label: str | None = Query(
        None,
        description="Optional label filter from golden samples (e.g. 'benign', 'malicious').",
    ),
) -> Dict[str, Any]:
    """
    Run the rules engine against golden telemetry samples.

    This is for offline evaluation / regression testing, not serving real traffic.
    """
    samples = load_golden_samples()

    if label is not None:
        samples = [s for s in samples if s["label"] == label]

    samples = samples[:limit]

    results: List[Dict[str, Any]] = []

    for idx, sample in enumerate(samples):
        telemetry: TelemetryInput = sample["telemetry"]
        sample_label = sample["label"]

        (
            threat_level,
            mitigations,
            rules_triggered,
            anomaly_score,
            ai_adv_score,
        ) = evaluate_rules(telemetry)

        effective_mitigations = _apply_enforcement_mode(mitigations)

        results.append(
            {
                "index": idx,
                "label": sample_label,
                "threat_level": threat_level,
                "rules_triggered": rules_triggered,
                "anomaly_score": anomaly_score,
                "ai_adversarial_score": ai_adv_score,
                "mitigations": [
                    {
                        "action": m.action,
                        "target": m.target,
                        "reason": m.reason,
                        "confidence": m.confidence,
                    }
                    for m in effective_mitigations
                ],
                "tenant_id": telemetry.tenant_id,
                "source": telemetry.source,
                "enforcement_mode": settings.enforcement_mode,
            }
        )

    logger.info(
        "defend_test_run",
        extra={
            "count": len(results),
            "limit": limit,
            "label_filter": label,
            "enforcement_mode": settings.enforcement_mode,
        },
    )

    return {
        "count": len(results),
        "limit": limit,
        "label_filter": label,
        "enforcement_mode": settings.enforcement_mode,
        "results": results,
    }


@app.get("/decisions")
async def decisions(
    tenant_id: str | None = Query(
        None,
        description="Optional tenant_id filter.",
    ),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """
    Inspect recent `/defend` decisions from in-memory history.

    This is an operational/debug endpoint, not part of the external contract.
    """
    items = list_decisions(tenant_id=tenant_id, limit=limit)

    logger.info(
        "decisions_query",
        extra={
            "tenant_id": tenant_id,
            "limit": limit,
            "returned": len(items),
        },
    )

    return {
        "count": len(items),
        "limit": limit,
        "tenant_id_filter": tenant_id,
        "results": items,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
