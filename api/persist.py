# api/persist.py
import json
import time
import logging
from sqlalchemy import text
from .db import engine

log = logging.getLogger("frostgate.persist")


def persist_decision(
    *,
    tenant_id: str,
    source: str,
    event_id: str,
    event_type: str,
    threat_level: str,
    anomaly_score: float,
    ai_adversarial_score: float,
    pq_fallback: bool,
    rules_triggered: list[str],
    explain_summary: str,
    latency_ms: int,
    request_obj: dict,
    response_obj: dict,
) -> None:
    started = time.time()
    payload = dict(
        tenant_id=tenant_id,
        source=source,
        event_id=event_id,
        event_type=event_type,
        threat_level=threat_level,
        anomaly_score=float(anomaly_score or 0.0),
        ai_adversarial_score=float(ai_adversarial_score or 0.0),
        pq_fallback=bool(pq_fallback),
        rules_triggered_json=json.dumps(rules_triggered or []),
        explain_summary=explain_summary or "",
        latency_ms=int(latency_ms or 0),
        request_json=json.dumps(request_obj or {}),
        response_json=json.dumps(response_obj or {}),
    )

    sql = text("""
        INSERT INTO decisions
        (tenant_id, source, event_id, event_type, threat_level,
         anomaly_score, ai_adversarial_score, pq_fallback,
         rules_triggered_json, explain_summary, latency_ms,
         request_json, response_json)
        VALUES
        (:tenant_id, :source, :event_id, :event_type, :threat_level,
         :anomaly_score, :ai_adversarial_score, :pq_fallback,
         :rules_triggered_json, :explain_summary, :latency_ms,
         :request_json, :response_json)
    """)

    try:
        with engine.begin() as c:
            c.execute(sql, payload)
        log.info(
            "persisted decision event_id=%s in %dms",
            event_id,
            int((time.time() - started) * 1000),
        )
    except Exception:
        log.exception(
            "FAILED to persist decision event_id=%s payload_keys=%s",
            event_id,
            list(payload.keys()),
        )
        raise
