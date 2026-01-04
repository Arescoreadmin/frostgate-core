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
s = p.read_text().splitlines(True)

# locate decorator line index
dec_i = None
for i, line in enumerate(s):
    if line.startswith('@router.get("/live"'):
        dec_i = i
        break
if dec_i is None:
    raise SystemExit('PATCH FAILED: @router.get("/live"...) not found')

# locate function def line after decorator: def or async def
fn_i = None
for j in range(dec_i+1, min(dec_i+40, len(s))):
    if re.match(r'^\s*(async\s+def|def)\s+feed_live\s*\(', s[j]):
        fn_i = j
        break
if fn_i is None:
    raise SystemExit("PATCH FAILED: feed_live def not found after decorator")

# determine indentation level of function body
# find first non-empty line after def
body_i = None
for k in range(fn_i+1, min(fn_i+2000, len(s))):
    if s[k].strip() == "":
        continue
    body_i = k
    break
if body_i is None:
    raise SystemExit("PATCH FAILED: can't find feed_live body")

indent = len(s[body_i]) - len(s[body_i].lstrip(" "))

# find end of function: first line with indentation < indent that is not blank/comment and is at column 0 (or less indent)
end_i = len(s)
for k in range(body_i, len(s)):
    line = s[k]
    if line.strip() == "":
        continue
    if line.lstrip().startswith("#"):
        continue
    cur_indent = len(line) - len(line.lstrip(" "))
    if cur_indent < indent and not line.startswith(" " * indent):
        end_i = k
        break

new_block = """@router.get("/live", response_model=FeedLiveResponse)
def feed_live(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    since_id: int | None = Query(default=None, ge=0),
    severity: str | None = Query(default=None),
    threat_level: str | None = Query(default=None),
    action_taken: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="search event_type/event_id/source"),
    only_changed: bool = Query(default=False),
    only_actionable: bool = Query(default=False),
):
    from api.db_models import DecisionRecord

    def clamp(x: float, lo: float, hi: float) -> float:
        return lo if x < lo else hi if x > hi else x

    THREAT_WEIGHT = {"none": 5.0, "low": 25.0, "medium": 55.0, "high": 85.0, "critical": 95.0}

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
        title = f"{et.upper()} {act.upper()} ({tl})"
        summary = f"{src} event. Score {score:.0f}. Action: {act}."

        return {"severity": _sev_from_threat(tl), "action_taken": act, "title": title, "summary": summary, "confidence": confidence, "score": score}

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
        meta = _loads_json_text(getattr(r, "request_json", None))

        rules_triggered, _score_from_diff, changed_fields, action_reason = _derive_from_diff(diff)

        pres = present(r)

        if only_changed and not changed_fields:
            continue

        if only_actionable and pres["action_taken"] not in ("challenge", "quarantine"):
            continue

        if action_taken and pres["action_taken"] != action_taken:
            continue

        ts = getattr(r, "created_at", None)
        ts_out = ts.isoformat() if ts is not None else None

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

        try:
            item = FeedItem(**_backfill_feed_item(item.model_dump()))
        except Exception:
            pass

        items.append(item)

    return FeedLiveResponse(items=items, next_since_id=max_id)
"""

# Replace from decorator to end of function
out = "".join(s[:dec_i] + [new_block, "\n\n"] + s[end_i:])
p.write_text(out)
print("✅ Overwrote feed_live with presentation engine (robust end-of-function detection)")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
