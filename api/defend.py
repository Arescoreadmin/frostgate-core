from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

# Contract: api.main imports `router`
router = APIRouter()


# -------------------------
# Helpers
# -------------------------

def _ensure_impacts(tie_d: dict | None, mitigations_count: int = 0) -> dict:
    """
    Ensure doctrine contract fields exist.
    service_impact + user_impact must be 0.0..1.0.
    """
    d = dict(tie_d or {})
    impact = max(0.0, min(1.0, 0.15 * float(mitigations_count)))
    d.setdefault("service_impact", impact)
    d.setdefault("user_impact", impact)
    d.setdefault("gating_decision", "allow")
    return d


def _to_utc(dt: datetime | str | None) -> datetime:
    """
    Accept datetime OR ISO-8601 string and normalize to timezone-aware UTC datetime.
    Handles trailing 'Z' and naive datetimes.
    """
    if dt is None:
        return datetime.now(timezone.utc)

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except Exception:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_id(source: Optional[str], tenant_id: Optional[str], ts_iso: str, payload: Dict[str, Any]) -> str:
    base = _canonical_json({"source": source, "tenant_id": tenant_id, "ts": ts_iso, "payload": payload})
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _get_event_type(body: "DefendRequest") -> str:
    if body.event_type:
        return body.event_type

    p = body.payload or {}
    e = body.event or {}

    if isinstance(p, dict):
        et = p.get("event_type") or p.get("type")
        if et:
            return str(et)
        pe = p.get("event")
        if isinstance(pe, dict):
            et = pe.get("event_type") or pe.get("type")
            if et:
                return str(et)

    if isinstance(e, dict):
        et = e.get("event_type") or e.get("type")
        if et:
            return str(et)

    return "unknown"


def _extract_auth_features(body: "DefendRequest") -> Tuple[int, Optional[str]]:
    p = body.payload or {}
    e = body.event or {}

    def dig_failed(obj: Any) -> Optional[int]:
        if not isinstance(obj, dict):
            return None
        v = obj.get("failed_auths")
        if v is None and isinstance(obj.get("features"), dict):
            v = obj["features"].get("failed_auths")
        if v is None and isinstance(obj.get("event"), dict):
            v = obj["event"].get("failed_auths")
        if v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None

    def dig_ip(obj: Any) -> Optional[str]:
        if not isinstance(obj, dict):
            return None
        for k in ("src_ip", "ip", "client_ip"):
            if obj.get(k):
                return str(obj.get(k))
        if isinstance(obj.get("features"), dict):
            for k in ("src_ip", "ip", "client_ip"):
                if obj["features"].get(k):
                    return str(obj["features"].get(k))
        if isinstance(obj.get("event"), dict):
            for k in ("src_ip", "ip", "client_ip"):
                if obj["event"].get(k):
                    return str(obj["event"].get(k))
        return None

    failed = dig_failed(p)
    if failed is None and isinstance(e, dict):
        try:
            failed = int(e.get("failed_auths")) if e.get("failed_auths") is not None else None
        except Exception:
            failed = None
    if failed is None:
        failed = 0

    src_ip = dig_ip(p) or (dig_ip(e) if isinstance(e, dict) else None)
    return failed, src_ip


# -------------------------
# Models
# -------------------------

class MitigationAction(BaseModel):
    action: Literal["block_ip", "rate_limit", "log_only"] = "log_only"
    target: Optional[str] = None
    ttl_seconds: int = 0
    reason: str = ""


class DecisionExplain(BaseModel):
    summary: str
    rules_triggered: List[str] = Field(default_factory=list)
    anomaly_score: float = 0.0
    llm_note: Optional[str] = None
    tie_d: Optional[dict[str, Any]] = None
    score: int = 0

    # Doctrine/invariants expected by tests
    persona: Optional[str] = None
    classification: Optional[str] = None
    roe_applied: bool = False
    disruption_limited: bool = False
    ao_required: bool = False


class DefendResponse(BaseModel):
    threat_level: Literal["none", "low", "medium", "high", "critical"]
    mitigations: List[MitigationAction] = Field(default_factory=list)
    explain: DecisionExplain
    ai_adversarial_score: float = 0.0
    pq_fallback: bool = False
    clock_drift_ms: int
    event_id: str


class DefendRequest(BaseModel):
    source: Optional[str] = None
    tenant_id: Optional[str] = None
    timestamp: Optional[str] = None
    classification: Optional[str] = None
    persona: Optional[str] = None

    event_type: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    event: Optional[Dict[str, Any]] = None


# -------------------------
# Doctrine
# -------------------------

def _apply_doctrine(body: DefendRequest, mitigations: List[MitigationAction], explain: DecisionExplain) -> None:
    persona = (body.persona or "").lower().strip() if body.persona else None
    classification = (body.classification or "").upper().strip() if body.classification else None

    explain.persona = persona
    explain.classification = classification
    explain.tie_d = _ensure_impacts({"persona": persona, "classification": classification}, len(mitigations))

    # Doctrine gating decision expected by tests:
    # guardian + SECRET-ish => require approval (conservative posture)
    if persona == "guardian" and classification in ("SECRET", "TOP_SECRET", "TS"):
        explain.tie_d["gating_decision"] = "require_approval"
    else:
        explain.tie_d["gating_decision"] = explain.tie_d.get("gating_decision", "allow")

    # Always present for tests
    explain.ao_required = False

    if persona == "guardian" and classification in ("SECRET", "TOP_SECRET", "TS"):
        explain.roe_applied = True

        # Guardian: cap disruption. Tests want <=1 block_ip
        blocks = [m for m in mitigations if m.action == "block_ip"]
        if len(blocks) > 1:
            keep = blocks[0]
            rebuilt: List[MitigationAction] = []
            kept = False
            for m in mitigations:
                if (m is keep) and not kept:
                    rebuilt.append(m)
                    kept = True
                elif m.action == "block_ip":
                    rebuilt.append(
                        MitigationAction(
                            action="rate_limit",
                            target=m.target,
                            ttl_seconds=900,
                            reason="guardian ROE cap",
                        )
                    )
                else:
                    rebuilt.append(m)
            mitigations[:] = rebuilt
            explain.disruption_limited = True
        else:
            explain.disruption_limited = False
    else:
        explain.roe_applied = False
        explain.disruption_limited = False


# -------------------------
# Endpoint
# -------------------------

@router.post("/defend", response_model=DefendResponse)
async def defend(
    body: DefendRequest,
    request: Request,
    x_pq_fallback: Optional[str] = Header(default=None),
) -> DefendResponse:
    t0 = datetime.now(timezone.utc)

    et = _get_event_type(body)
    et_l = et.lower()

    failed, src_ip = _extract_auth_features(body)

    threat_level: Literal["none", "low", "medium", "high", "critical"] = "none"
    score = 0
    rules: List[str] = []
    mitigations: List[MitigationAction] = []

    if et_l in ("auth", "authentication", "login") or "auth" in et_l:
        if failed >= 10:
            threat_level = "high"
            score = 90
            rules.append("auth:bruteforce_high")
            if src_ip:
                mitigations.append(
                    MitigationAction(action="block_ip", target=src_ip, ttl_seconds=3600, reason="high failed_auths")
                )
        elif failed >= 5:
            threat_level = "medium"
            score = 60
            rules.append("auth:bruteforce_medium")
            if src_ip:
                mitigations.append(
                    MitigationAction(action="rate_limit", target=src_ip, ttl_seconds=900, reason="elevated failed_auths")
                )
        elif failed > 0:
            threat_level = "low"
            score = 25
            rules.append("auth:bruteforce_low")

    explain = DecisionExplain(
        summary=f"{et} evaluated",
        rules_triggered=rules,
        anomaly_score=0.0,
        llm_note=None,
        tie_d=None,
        score=score,
    )

    _apply_doctrine(body, mitigations, explain)

    ts_iso = _to_utc(body.timestamp).isoformat().replace("+00:00", "Z")
    eid = _event_id(body.source, body.tenant_id, ts_iso, body.payload or {})

    drift_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)

    return DefendResponse(
        threat_level=threat_level,
        mitigations=mitigations,
        explain=explain,
        ai_adversarial_score=0.0,
        pq_fallback=(x_pq_fallback in ("1", "true", "True", "yes", "on")),
        clock_drift_ms=max(drift_ms, 0),
        event_id=eid,
    )
