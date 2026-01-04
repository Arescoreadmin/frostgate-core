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
import re, py_compile

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Ensure DecisionRecord import points to db_models, not api.db
s = re.sub(r'(?m)^from api\.db import (.*\bDecisionRecord\b.*)$',
           r'from api.db_models import DecisionRecord', s)

# Find the /live handler block by its decorator and replace the whole function implementation.
# We replace from the decorator line through the end of the function (next top-level decorator or EOF).
pat = re.compile(r'(?ms)^@router\.get\("/live"[^\n]*\)\n(def feed_live\([\s\S]*?\n)(?=^@router\.|^\Z)')
m = pat.search(s)
if not m:
    raise SystemExit('PATCH FAILED: could not locate @router.get("/live"... ) feed_live block')

# Keep the original function signature (params), but replace body.
sig = m.group(1)  # starts at "def feed_live(...):\n"
# Split signature into header and existing body
# Find first indented line after signature header
hdr_m = re.match(r'(?s)(def feed_live\([\s\S]*?\):\n)', sig)
if not hdr_m:
    raise SystemExit("PATCH FAILED: couldn't parse feed_live signature header")
hdr = hdr_m.group(1)

new_body = """
    # --- stable live feed handler ---
    # Notes:
    # - DB model uses created_at; API exposes timestamp
    # - 'severity' query param is an alias for threat_level
    qry = db.query(DecisionRecord)

    # alias: severity -> threat_level (DB has threat_level only)
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

    # basic search across a few fields that actually exist
    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (DecisionRecord.event_type.ilike(like))
            | (DecisionRecord.event_id.ilike(like))
        )

    qry = qry.order_by(DecisionRecord.id.desc()).limit(limit)
    rows = qry.all()

    items = []
    max_id = since_id or 0

    # If helper exists in this module, use it. Otherwise, no-op.
    backfill = globals().get("_backfill_feed_item", lambda x: x)

    for r in rows:
        max_id = max(max_id, int(r.id))

        ts = getattr(r, "created_at", None)
        ts_iso = ts.isoformat() if ts else None

        diff = globals().get("_loads_json_text", lambda v: v)(getattr(r, "decision_diff_json", None))
        rules_triggered, score, changed_fields, action_reason = globals().get("_derive_from_diff", lambda d: ([], None, [], None))(diff)

        item = {
            "id": int(r.id),
            "event_id": getattr(r, "event_id", None),
            "event_type": getattr(r, "event_type", None),
            "source": getattr(r, "source", None),
            "tenant_id": getattr(r, "tenant_id", None),
            "threat_level": getattr(r, "threat_level", None),

            # the whole point
            "timestamp": ts_iso,

            # derived/backfilled fields (your UI wants these)
            "severity": None,
            "title": None,
            "summary": None,
            "action_taken": None,
            "confidence": None,
            "score": score,
            "rules_triggered": rules_triggered or [],
            "changed_fields": changed_fields or [],
            "action_reason": action_reason,
            "decision_diff": diff,
            "metadata": None,
            "decision_id": None,
            "fingerprint": None,
        }

        item = backfill(item)
        items.append(item)

    return {"items": items, "next_since_id": max_id}
"""

replacement = '@router.get("/live", response_model=FeedLiveResponse)\n' + hdr + new_body + "\n"
s = s[:m.start()] + replacement + s[m.end():]

p.write_text(s, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("✅ Patched /live: timestamp now maps from DecisionRecord.created_at")
PY
