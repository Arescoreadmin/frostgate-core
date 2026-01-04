#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/feed.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from __future__ import annotations
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

# Find the /live route decorator and function start
m = re.search(r'(?m)^@router\.get\("/live".*?\n^def\s+feed_live\s*\(', s)
if not m:
    raise SystemExit('PATCH FAILED: could not find @router.get("/live"... ) + def feed_live(')

start = m.start()

# Find end of function: next top-level decorator or def at column 0 after the feed_live def
# We locate the def line after the decorator, then search forward for next "^@router" or "^def " at col 0.
def_line = re.search(r'(?m)^def\s+feed_live\s*\(', s[m.end()-1:])
if not def_line:
    raise SystemExit("PATCH FAILED: def feed_live not found after decorator")
def_abs = (m.end()-1) + def_line.start()

# Search for next top-level marker after def_abs
m_end = re.search(r'(?m)^(?:@router\.|def\s+)\w', s[def_abs+1:])
end = len(s) if not m_end else (def_abs+1) + m_end.start()

new_block = r'''@router.get("/live", response_model=FeedLiveResponse)
def feed_live(
    db: Session = Depends(get_db),

    # pagination/incremental
    limit: int = Query(default=50, ge=1, le=200),
    since_id: int | None = Query(default=None, ge=0),

    # filters
    severity: str | None = Query(default=None),
    threat_level: str | None = Query(default=None),
    action_taken: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="search title/summary/event_type/event_id"),

    # toggles
    only_changed: bool = Query(default=False),
    only_actionable: bool = Query(default=False),
):
    """
    Live feed: reads DecisionRecord rows and presents them as FeedItem.

    IMPORTANT:
    - DB schema is minimal (no severity/action/title/summary fields stored).
    - We compute deterministic presentation fields from threat_level + scores.
    - 'severity' query param is a UI alias for threat_level.
    """
    # DecisionRecord is in db_models, not api.db
    from api.db_models import DecisionRecord

    def clamp(x: float, lo: float, hi: float) -> float:
        return lo if x < lo else hi if x > hi else x

    THREAT_WEIGHT = {
        "none": 5.0,
        "low": 25.0,
        "medium": 55.0,
        "high": 85.0,
        "critical": 95.0,
    }

    def present(r) -> dict:
        tl = (getattr(r, "threat_level", None) or "none").lower()
        anomaly = getattr(r, "anomaly_score", None)
        adv = getattr(r, "ai_adversarial_score", None)

        anomaly = float(anomaly) if isinstance(anomaly, (int, float)) else 0.0
        adv = float(adv) if isinstance(adv, (int, float)) else 0.0

        tw = THREAT_WEIGHT.get(tl, 10.0)
        score = max(tw, anomaly * 100.0, adv * 100.0)
        score = clamp(score, 0.0, 100.0)

        confidence = clamp(0.5 + (score / 200.0), 0.0, 1.0)

        if score >= 85.0 or (tl in ("high", "critical") and adv >= 0.6):
            act = "quarantine"
        elif score >= 65.0:
            act = "challenge"
        else:
            act = "log_only"

        et = (getattr(r, "event_type", None) or "event").lower()
        src = (getattr(r, "source", None) or "unknown").lower()

        if et == "waf":
            title = f"WAF {act.upper()} ({tl})"
            summary = f"{src} flagged request. Score {score:.0f}. Action: {act}."
        elif et in ("edr", "process"):
            title = f"EDR {act.upper()} ({tl})"
            summary = f"{src} detected suspicious process behavior. Score {score:.0f}. Action: {act}."
        elif et in ("auth", "login"):
            title = f"AUTH {act.upper()} ({tl})"
            summary = f"{src} detected abnormal authentication pattern. Score {score:.0f}. Action: {act}."
        else:
            title = f"{et.upper()} {act.upper()} ({tl})"
            summary = f"{src} event. Score {score:.0f}. Action: {act}."

        return {
            "severity": _sev_from_threat(tl),
            "action_taken": act,
            "title": title,
            "summary": summary,
            "confidence": confidence,
            "score": score,
        }

    qry = db.query(DecisionRecord)

    # severity is alias for threat_level
    if (not threat_level) and severity:
        threat_level = severity

    if since_id is not None:
        qry = qry.filter(DecisionRecord.id > since_id)

    if threat_level:
        qry = qry.filter(DecisionRecord.threat_level == threat_level)

    if source:
        qry = qry.filter(DecisionRecord.source == source)

    if tenant_id:
        qry = qry.filter(DecisionRecord.tenant_id == tenant_id)

    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (DecisionRecord.event_type.ilike(like))
            | (DecisionRecord.event_id.ilike(like))
            | (DecisionRecord.source.ilike(like))
        )

    qry = qry.order_by(DecisionRecord.id.desc()).limit(limit)
    rows = qry.all()

    items: list[FeedItem] = []
    max_id = since_id or 0

    for r in rows:
        max_id = max(max_id, int(r.id))

        diff = _loads_json_text(getattr(r, "decision_diff_json", None))
        meta = _loads_json_text(getattr(r, "request_json", None))  # best available "metadata-ish" payload

        rules_triggered, score_from_diff, changed_fields, action_reason = _derive_from_diff(diff)

        # presentation engine (fills non-null fields)
        pres = present(r)

        # only_changed toggle: if there is no diff/changes, skip
        if only_changed and not changed_fields:
            continue

        # only_actionable: challenge/quarantine only
        if only_actionable and pres["action_taken"] not in ("challenge", "quarantine"):
            continue

        # action_taken filter (applies to computed action)
        if action_taken and pres["action_taken"] != action_taken:
            continue

        # timestamp: map from created_at
        ts = getattr(r, "created_at", None)
        ts_out = ts.isoformat() if ts is not None else None

        # build item
        item = FeedItem(
            id=int(r.id),
            event_id=getattr(r, "event_id", None),
            event_type=getattr(r, "event_type", None),
            source=getattr(r, "source", None),
            tenant_id=getattr(r, "tenant_id", None),
            threat_level=getattr(r, "threat_level", None),
            decision_id=None,
            timestamp=ts_out,

            severity=pres["severity"],
            title=pres["title"],
            summary=pres["summary"],
            action_taken=pres["action_taken"],
            confidence=pres["confidence"],
            score=pres["score"],

            rules_triggered=rules_triggered or [],
            changed_fields=changed_fields or [],
            action_reason=action_reason,
            fingerprint=None,
            decision_diff=diff,
            metadata=meta,
        )

        # backfill any remaining null-ish fields your existing helper handles
        try:
            item = FeedItem(**_backfill_feed_item(item.model_dump()))
        except Exception:
            # don't die in the feed because presentation is slightly off
            pass

        items.append(item)

    return FeedLiveResponse(items=items, next_since_id=max_id)
'''

# Replace block
s2 = s[:start] + new_block + "\n\n" + s[end:]
p.write_text(s2)
print('✅ Overwrote /live handler with deterministic presentation engine')
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
