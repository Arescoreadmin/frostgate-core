#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/feed.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text(encoding="utf-8")

# Heuristic: locate the function that serializes DecisionRecord -> dict (often named _to_item / serialize_* / to_dict)
# We will inject a small "backfill" helper and apply it where items are built.

if "def _backfill_feed_item" not in s:
    helper = r'''
def _sev_from_threat(threat: str | None) -> str:
    t = (threat or "").strip().lower()
    if t in ("critical",):
        return "critical"
    if t in ("high",):
        return "high"
    if t in ("medium",):
        return "medium"
    if t in ("low",):
        return "low"
    return "info"

def _backfill_feed_item(i: dict) -> dict:
    # timestamp
    if not i.get("timestamp"):
        # prefer created_at if present
        ca = i.get("created_at")
        i["timestamp"] = ca or None

    # severity
    if not i.get("severity"):
        i["severity"] = _sev_from_threat(i.get("threat_level"))

    # action_taken
    if not i.get("action_taken"):
        # attempt to infer from decision_diff summary/prev/curr
        dd = i.get("decision_diff") or {}
        summ = (dd.get("summary") or "").lower()
        if "block" in summ or "blocked" in summ:
            i["action_taken"] = "blocked"
        elif "rate" in summ:
            i["action_taken"] = "rate_limited"
        else:
            i["action_taken"] = "log_only"

    # title / summary
    if not i.get("title"):
        et = i.get("event_type") or "event"
        src = i.get("source") or "unknown"
        i["title"] = f"{et} from {src}"
    if not i.get("summary"):
        sev = i.get("severity") or "info"
        thr = i.get("threat_level") or ""
        act = i.get("action_taken") or ""
        i["summary"] = f"{sev}/{thr} {act}".strip()

    # confidence / score
    if i.get("confidence") is None:
        sev = (i.get("severity") or "info").lower()
        i["confidence"] = 0.95 if sev in ("critical","high") else 0.75
    if i.get("score") is None:
        thr = (i.get("threat_level") or "").lower()
        i["score"] = 90 if thr in ("critical","high") else (60 if thr == "medium" else 0)

    # rules_triggered always list
    if i.get("rules_triggered") is None:
        i["rules_triggered"] = []

    # changed_fields always list
    if i.get("changed_fields") is None:
        i["changed_fields"] = []

    return i
'''
    # Insert helper near top after imports (first blank line after imports block)
    m = re.search(r"\n\n", s)
    ins = m.end() if m else 0
    s = s[:ins] + helper + "\n" + s[ins:]

# Now find where the item dict is constructed. Common pattern: items.append({...}) or item = {...}
# We patch any `items.append(item)` or `return {"items": items, ...}` stage by mapping backfill.

# If code has "items =" list and later returns, inject backfill before return.
if "_backfill_feed_item" in s:
    # Patch: before returning response with items, apply backfill in-place
    # Try a few patterns.
    patterns = [
        r"(return\s+\{\s*\"items\"\s*:\s*)(items)(\s*,)",
        r"(return\s+\{\s*'items'\s*:\s*)(items)(\s*,)",
    ]
    for pat in patterns:
        if re.search(pat, s):
            s = re.sub(
                pat,
                r"\1[_backfill_feed_item(x) for x in \2]\3",
                s,
                count=1,
            )
            break

# Also patch where items list is returned without dict wrapper, rare but possible
if re.search(r"return\s+items\s*$", s, flags=re.M):
    s = re.sub(r"return\s+items\s*$", r"return [_backfill_feed_item(x) for x in items]", s, flags=re.M)

p.write_text(s, encoding="utf-8")
print("✅ Patched feed backfill helpers into api/feed.py")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
