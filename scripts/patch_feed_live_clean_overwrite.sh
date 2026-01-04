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
import sys

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# We will replace the whole decorator+function for @router.get("/live", ...)
pat = re.compile(
    r'(?ms)^@router\.get\("/live",\s*response_model=FeedLiveResponse\)\s*\n'
    r"def\s+feed_live\s*\(.*?\)\s*:\s*\n"
    r"(?:^[ \t].*\n)*"
)

m = pat.search(s)
if not m:
    print("PATCH FAILED: couldn't find @router.get(\"/live\", response_model=FeedLiveResponse) + feed_live() block", file=sys.stderr)
    sys.exit(2)

replacement = r'''@router.get("/live", response_model=FeedLiveResponse)
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
    Live feed view.
    NOTE: DB model has threat_level, not severity. `severity` is treated as an alias for `threat_level`.
    """
    # severity is a UI alias for threat_level
    if (not threat_level) and severity:
        threat_level = severity

    qry = db.query(DecisionRecord)

    if since_id is not None:
        qry = qry.filter(DecisionRecord.id > since_id)

    if threat_level:
        qry = qry.filter(DecisionRecord.threat_level == threat_level)

    if action_taken:
        qry = qry.filter(DecisionRecord.action_taken == action_taken)

    if source:
        qry = qry.filter(DecisionRecord.source == source)

    if tenant_id:
        qry = qry.filter(DecisionRecord.tenant_id == tenant_id)

    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (DecisionRecord.title.ilike(like))
            | (DecisionRecord.summary.ilike(like))
            | (DecisionRecord.event_type.ilike(like))
            | (DecisionRecord.event_id.ilike(like))
            | (DecisionRecord.decision_id.ilike(like))
        )

    # newest first for initial load
    qry = qry.order_by(DecisionRecord.id.desc()).limit(limit)
    rows = qry.all()

    items: list[dict] = []
    max_id = since_id or 0

    for r in rows:
        rid = int(getattr(r, "id", 0) or 0)
        max_id = max(max_id, rid)

        diff = _loads_json_text(getattr(r, "decision_diff_json", None))
        meta = _loads_json_text(getattr(r, "metadata_json", None))

        rules_triggered, score, changed_fields, action_reason = _derive_from_diff(diff)

        # base dict that is JSON-safe
        it = {
            "id": rid,
            "event_id": getattr(r, "event_id", None),
            "event_type": getattr(r, "event_type", None),
            "source": getattr(r, "source", None),
            "tenant_id": getattr(r, "tenant_id", None),
            "threat_level": getattr(r, "threat_level", None),
            "decision_id": getattr(r, "decision_id", None),

            # these may be null in older records; backfill will fix them
            "timestamp": getattr(r, "timestamp", None),
            "severity": getattr(r, "severity", None),
            "title": getattr(r, "title", None),
            "summary": getattr(r, "summary", None),
            "action_taken": getattr(r, "action_taken", None),

            "confidence": getattr(r, "confidence", None),
            "score": score if score is not None else getattr(r, "score", None),
            "rules_triggered": rules_triggered,
            "changed_fields": changed_fields,
            "action_reason": action_reason,
            "fingerprint": getattr(r, "fingerprint", None),
            "decision_diff": diff,
            "metadata": meta,
        }

        # toggles (after we have changed_fields etc.)
        if only_changed and not it.get("changed_fields"):
            continue

        # actionability heuristic (do NOT reference DecisionRecord.severity; it may not exist)
        actionable = True
        if it.get("action_taken") in ("log_only", "allow") and (it.get("threat_level") in ("none", "low")):
            actionable = False
        if only_actionable and not actionable:
            continue

        # Backfill computed fields so UI never sees nulls
        it = _backfill_feed_item(it)

        items.append(it)

    return {
        "items": items,
        "next_since_id": max_id,
    }
'''

s2 = s[:m.start()] + replacement + s[m.end():]
p.write_text(s2, encoding="utf-8")

import py_compile
py_compile.compile(str(p), doraise=True)
print("✅ Overwrote feed_live cleanly (no DecisionRecord.severity usage).")
print("✅ Compile OK: api/feed.py")
PY
