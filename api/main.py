from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from fastapi import FastAPI, Request, Header, Query, Depends
from loguru import logger

from .schemas import (
    TelemetryInput,
    DefendResponse,
    ExplainBlock,
    MitigationAction,
    TIEDEstimate,
)
from .config import settings
from .logging_config import configure_logging
from .auth import require_api_key
from engine import evaluate_rules, record_decision, list_decisions
from tools.telemetry.loader import load_golden_samples

# Configure logging before app starts
configure_logging()

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
ANCHOR_STATE_FILE = STATE_DIR / "merkle_anchor_status.json"
CHAOS_STATE_FILE = STATE_DIR / "chaos_status.json"

app = FastAPI(
    title="FrostGate Core API",
    version="0.8.0",
    description=(
        "FrostGate Core MVP: rules engine, golden-sample tester, "
        "structured logging, decision history, enforcement modes, "
        "Merkle anchor status, chaos job status, and API key auth."
    ),
)


@app.on_event("startup")
async def startup_event() -> None:
    logger.info(
        "service_start",
        extra={
            "env": settings.env,
            "service": "frostgate-core",
            "enforcement_mode": settings.enforcement_mode,
            "auth_enabled": bool(settings.api_key),
        },
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    logger.debug("health_check")
    return {
        "status": "ok",
        "env": settings.env,
        "enforcement_mode": settings.enforcement_mode,
        "auth_enabled": bool(settings.api_key),
    }


@app.get("/status", dependencies=[Depends(require_api_key)])
@app.get("/v1/status", dependencies=[Depends(require_api_key)])
async def status() -> Dict[str, Any]:
    anchor_status = None
    chaos_status = None

    try:
        import json

        if ANCHOR_STATE_FILE.exists():
            anchor_status = json.loads(ANCHOR_STATE_FILE.read_text())
        if CHAOS_STATE_FILE.exists():
            chaos_status = json.loads(CHAOS_STATE_FILE.read_text())
    except Exception:
        # Keep it defensive; don't break /status because of bad state
        pass

    return {
        "service": "frostgate-core",
        "version": "0.8.0",
        "env": settings.env,
        "enforcement_mode": settings.enforcement_mode,
        "components": {
            "ensemble": "rules-only-mvp",
            "merkle_anchor": "stub",
            "supervisor": "pending",
            "chaos": "stub",
        },
        "anchor": anchor_status,
        "chaos": chaos_status,
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


@app.post(
    "/defend",
    response_model=DefendResponse,
    dependencies=[Depends(require_api_key)],
)
@app.post(
    "/v1/defend",
    response_model=DefendResponse,
    dependencies=[Depends(require_api_key)],
)
async def defend(
    telemetry: TelemetryInput,
    request: Request,
    x_pq_fallback: str | None = Header(default=None, alias=settings.pq_fallback_header),
) -> DefendResponse:
    """
    Primary `/defend` endpoint backed by the rules engine.
    Also records decisions into in-memory history.

    Doctrine / ROE metadata is attached when persona/classification is present
    (v1 clients), but the core decision is still rules-first.
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

    # --- Doctrine / ROE MVP wiring ---
    # Only attach doctrine metadata if persona/classification came in.
    persona = getattr(telemetry, "persona", None)
    classification = getattr(telemetry, "classification", None)

    if persona or classification:
        explain.persona = persona
        explain.classification = classification

        # Dirt-simple TIED heuristic; tests only care that it exists & is shaped.
        service_impact = 0.8 if threat_level == "high" else 0.5
        user_impact = 0.7 if threat_level == "high" else 0.4

        # Map threat_level into the allowed contract values
        if threat_level in ("medium", "high"):
            gating_decision = "require_approval"
        else:
            gating_decision = "allow"

        explain.tie_d = TIEDEstimate(
            service_impact=service_impact,
            user_impact=user_impact,
            gating_decision=gating_decision,
            notes="MVP heuristic; rules-only engine + doctrine stub",
        )

        # ROE flags: this is now a doctrine-shaped decision, even if
        # mitigations are unchanged for MVP.
        explain.roe_applied = True
        explain.disruption_limited = False
        explain.ao_required = False
    # --- end doctrine wiring ---

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


@app.get(
    "/defend/test",
    dependencies=[Depends(require_api_key)],
)
@app.get(
    "/v1/defend/test",
    dependencies=[Depends(require_api_key)],
)
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


@app.get(
    "/decisions",
    dependencies=[Depends(require_api_key)],
)
@app.get(
    "/v1/decisions",
    dependencies=[Depends(require_api_key)],
)
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


@app.get(
    "/anchor/status",
    dependencies=[Depends(require_api_key)],
)
@app.get(
    "/v1/anchor/status",
    dependencies=[Depends(require_api_key)],
)
async def anchor_status() -> Dict[str, Any]:
    """
    Expose last Merkle anchor job status.

    Backed by state/merkle_anchor_status.json written by the merkle-anchor job.
    """
    if not ANCHOR_STATE_FILE.exists():
        logger.warning("anchor_status_missing", extra={"state_file": str(ANCHOR_STATE_FILE)})
        return {"status": "unknown", "detail": "no_anchor_state"}

    try:
        import json

        payload = json.loads(ANCHOR_STATE_FILE.read_text())
        return payload
    except Exception as exc:
        logger.error(
            "anchor_status_read_error",
            extra={"state_file": str(ANCHOR_STATE_FILE), "error": str(exc)},
        )
        return {"status": "error", "detail": "failed_to_read_anchor_state"}


@app.get(
    "/chaos/status",
    dependencies=[Depends(require_api_key)],
)
@app.get(
    "/v1/chaos/status",
    dependencies=[Depends(require_api_key)],
)
async def chaos_status() -> Dict[str, Any]:
    """
    Expose last chaos-monkey job status.

    Backed by state/chaos_status.json written by the chaos job.
    """
    if not CHAOS_STATE_FILE.exists():
        logger.warning("chaos_status_missing", extra={"state_file": str(CHAOS_STATE_FILE)})
        return {"status": "unknown", "detail": "no_chaos_state"}

    try:
        import json

        payload = json.loads(CHAOS_STATE_FILE.read_text())
        return payload
    except Exception as exc:
        logger.error(
            "chaos_status_read_error",
            extra={"state_file": str(CHAOS_STATE_FILE), "error": str(exc)},
        )
        return {"status": "error", "detail": "failed_to_read_chaos_state"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
