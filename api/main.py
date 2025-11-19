from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from fastapi import FastAPI, Request, Header, Query, Depends
from loguru import logger

from .schemas import TelemetryInput, DefendResponse, ExplainBlock, MitigationAction
from .config import settings
from .logging_config import configure_logging
from .auth import require_api_key

from engine import (
    evaluate_rules,
    evaluate_with_doctrine,
    record_decision,
    list_decisions,
)

from tools.telemetry.loader import load_golden_samples


# ---------------------------------------------------------
# Logging config
# ---------------------------------------------------------
configure_logging()

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
ANCHOR_STATE_FILE = STATE_DIR / "merkle_anchor_status.json"
CHAOS_STATE_FILE = STATE_DIR / "chaos_status.json"


# ---------------------------------------------------------
# Helpers: normalize Pydantic models → dicts for V2 compatibility
# ---------------------------------------------------------
def _mitigation_to_dict(m: MitigationAction | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(m, dict):
        return m
    if hasattr(m, "model_dump"):
        return m.model_dump()

    return {
        "action": m.action,
        "target": m.target,
        "reason": m.reason,
        "confidence": m.confidence,
    }


def _explain_to_dict(ex: ExplainBlock | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(ex, dict):
        return ex
    if hasattr(ex, "model_dump"):
        return ex.model_dump()

    out = {
        "summary": ex.summary,
        "rules_triggered": ex.rules_triggered,
        "anomaly_score": ex.anomaly_score,
        "llm_note": ex.llm_note,
    }

    for attr in (
        "classification",
        "persona",
        "tie_d",
        "roe_applied",
        "disruption_limited",
        "ao_required",
    ):
        if hasattr(ex, attr):
            out[attr] = getattr(ex, attr)

    return out


def _apply_enforcement_mode(mitigations: List[Any]) -> List[Dict[str, Any]]:
    base = [_mitigation_to_dict(m) for m in mitigations]

    if settings.enforcement_mode != "observe":
        return base

    out = []
    for m in base:
        out.append(
            {
                **m,
                "action": "log_only",
                "reason": f"[observe-only] {m.get('reason','')}",
            }
        )
    return out


# ---------------------------------------------------------
# Lifespan
# ---------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "service_start",
        extra={
            "env": settings.env,
            "service": "frostgate-core",
            "enforcement_mode": settings.enforcement_mode,
            "auth_enabled": bool(settings.api_key),
        },
    )
    yield


app = FastAPI(
    title="FrostGate Core API",
    version="0.8.0",
    description="FrostGate Core MVP w/ doctrine + rules + TIED",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# Endpoints
# ---------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "env": settings.env,
        "enforcement_mode": settings.enforcement_mode,
        "auth_enabled": bool(settings.api_key),
    }


@app.get("/status", dependencies=[Depends(require_api_key)])
@app.get("/v1/status", dependencies=[Depends(require_api_key)])
async def status() -> Dict[str, Any]:
    import json

    anchor_status = None
    chaos_status = None

    try:
        if ANCHOR_STATE_FILE.exists():
            anchor_status = json.loads(ANCHOR_STATE_FILE.read_text())
        if CHAOS_STATE_FILE.exists():
            chaos_status = json.loads(CHAOS_STATE_FILE.read_text())
    except Exception:
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


# ---------------------------------------------------------
# Core defend endpoint with doctrine/TIED
# ---------------------------------------------------------
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
):
    now = datetime.now(timezone.utc)

    try:
        ts = datetime.fromisoformat(telemetry.timestamp.replace("Z", "+00:00"))
        clock_drift_ms = int((now - ts).total_seconds() * 1000)
    except Exception:
        clock_drift_ms = 0

    (
        threat_level,
        mitigations,
        rules,
        anomaly_score,
        ai_adv,
    ) = evaluate_rules(telemetry)

    pq_fallback = bool(x_pq_fallback)

    explain_base = ExplainBlock(
        summary=f"MVP decision for tenant={telemetry.tenant_id}, source={telemetry.source}",
        rules_triggered=rules,
        anomaly_score=anomaly_score,
        llm_note=f"MVP stub, enforcement_mode={settings.enforcement_mode}",
    )

    doctrine_on = getattr(telemetry, "persona", None) or getattr(telemetry, "classification", None)

    if doctrine_on:
        decision = evaluate_with_doctrine(
            telemetry=telemetry,
            base_threat_level=threat_level,
            base_mitigations=mitigations,
            base_explain=explain_base,
            base_ai_adv_score=ai_adv,
            pq_fallback=pq_fallback,
            clock_drift_ms=clock_drift_ms,
        )

        threat_level = decision.threat_level
        mitigations = decision.mitigations
        explain = _explain_to_dict(decision.explain)
        ai_adv = decision.ai_adversarial_score
        pq_fallback = decision.pq_fallback
        clock_drift_ms = decision.clock_drift_ms

    else:
        explain = _explain_to_dict(explain_base)

    effective_mitigations = _apply_enforcement_mode(mitigations)

    resp = DefendResponse(
        threat_level=threat_level,
        mitigations=effective_mitigations,
        explain=explain,
        ai_adversarial_score=ai_adv,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )

    record_decision(
        tenant_id=telemetry.tenant_id,
        source=telemetry.source,
        threat_level=threat_level,
        rules_triggered=rules,
        anomaly_score=anomaly_score,
        ai_adv_score=ai_adv,
        pq_fallback=pq_fallback,
        clock_drift_ms=clock_drift_ms,
    )

    logger.info(
        "defend_decision",
        extra={
            "tenant_id": telemetry.tenant_id,
            "source": telemetry.source,
            "threat_level": threat_level,
            "rules": rules,
            "anomaly_score": anomaly_score,
            "ai_adv_score": ai_adv,
            "pq_fallback": pq_fallback,
            "clock_drift_ms": clock_drift_ms,
            "enforcement_mode": settings.enforcement_mode,
            "mitigation_count": len(effective_mitigations),
        },
    )

    return resp


# ---------------------------------------------------------
# Golden-sample engine test
# ---------------------------------------------------------
@app.get("/v1/defend/test", dependencies=[Depends(require_api_key)])
@app.get("/defend/test", dependencies=[Depends(require_api_key)])
async def defend_test(
    limit: int = Query(10, ge=1, le=100),
    label: str | None = None,
):
    samples = load_golden_samples()
    if label:
        samples = [s for s in samples if s["label"] == label]

    out = []
    for idx, s in enumerate(samples[:limit]):
        telemetry = s["telemetry"]
        lbl = s["label"]

        (
            threat,
            mitigations,
            rules,
            anomaly,
            ai,
        ) = evaluate_rules(telemetry)

        mitigations = _apply_enforcement_mode(mitigations)

        out.append(
            {
                "index": idx,
                "label": lbl,
                "threat_level": threat,
                "rules_triggered": rules,
                "anomaly_score": anomaly,
                "ai_adversarial_score": ai,
                "mitigations": [_mitigation_to_dict(m) for m in mitigations],
                "tenant_id": telemetry.tenant_id,
                "source": telemetry.source,
                "enforcement_mode": settings.enforcement_mode,
            }
        )

    return {
        "count": len(out),
        "limit": limit,
        "label_filter": label,
        "results": out,
    }


# ---------------------------------------------------------
# Decisions history
# ---------------------------------------------------------
@app.get("/decisions", dependencies=[Depends(require_api_key)])
@app.get("/v1/decisions", dependencies=[Depends(require_api_key)])
async def decisions(
    tenant_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    items = list_decisions(tenant_id=tenant_id, limit=limit)
    return {"count": len(items), "limit": limit, "results": items}


# ---------------------------------------------------------
# Anchor + Chaos job status
# ---------------------------------------------------------
@app.get("/anchor/status", dependencies=[Depends(require_api_key)])
@app.get("/v1/anchor/status", dependencies=[Depends(require_api_key)])
async def anchor_status():
    import json
    if not ANCHOR_STATE_FILE.exists():
        return {"status": "unknown"}
    try:
        return json.loads(ANCHOR_STATE_FILE.read_text())
    except Exception:
        return {"status": "error"}


@app.get("/chaos/status", dependencies=[Depends(require_api_key)])
@app.get("/v1/chaos/status", dependencies=[Depends(require_api_key)])
async def chaos_status():
    import json
    if not CHAOS_STATE_FILE.exists():
        return {"status": "unknown"}
    try:
        return json.loads(CHAOS_STATE_FILE.read_text())
    except Exception:
        return {"status": "error"}


# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
