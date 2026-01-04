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

# 1) Ensure helper exists (insert once)
if "def _backfill_feed_item" not in s:
    insert = r'''
from datetime import datetime, timezone

def _sev_from_threat(threat: str | None) -> str:
    t = (threat or "").strip().lower()
    if t == "critical": return "critical"
    if t == "high": return "high"
    if t == "medium": return "medium"
    if t == "low": return "low"
    return "info"

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _backfill_feed_item(i: dict) -> dict:
    # Timestamp
    if not i.get("timestamp"):
        ca = i.get("created_at")
        i["timestamp"] = ca or _now_iso()

    # Severity
    if not i.get("severity"):
        i["severity"] = _sev_from_threat(i.get("threat_level"))

    # Action taken
    if not i.get("action_taken"):
        dd = i.get("decision_diff") or {}
        summ = (dd.get("summary") or "").lower()
        if "block" in summ or "blocked" in summ:
            i["action_taken"] = "blocked"
        elif "rate" in summ:
            i["action_taken"] = "rate_limited"
        else:
            # if we have a threat, default to log_only (not blank)
            i["action_taken"] = "log_only"

    # Title / Summary
    if not i.get("title"):
        et = i.get("event_type") or "event"
        src = i.get("source") or "unknown"
        i["title"] = f"{et} from {src}"
    if not i.get("summary"):
        sev = i.get("severity") or "info"
        thr = (i.get("threat_level") or "").strip()
        act = (i.get("action_taken") or "").strip()
        i["summary"] = f"{sev} {thr} {act}".strip()

    # Confidence / Score
    if i.get("confidence") is None:
        sev = (i.get("severity") or "info").lower()
        i["confidence"] = 0.95 if sev in ("critical","high") else 0.75
    if i.get("score") is None:
        thr = (i.get("threat_level") or "").lower()
        i["score"] = 90 if thr in ("critical","high") else (60 if thr == "medium" else 0)

    if i.get("rules_triggered") is None:
        i["rules_triggered"] = []
    if i.get("changed_fields") is None:
        i["changed_fields"] = []

    return i
'''
    # insert after imports block (after first double newline)
    m = re.search(r"\n\n", s)
    idx = m.end() if m else 0
    s = s[:idx] + insert + "\n" + s[idx:]

# 2) Patch common pattern: item = {...}\n items.append(item)
# Insert backfill line right before append
pattern = r"(item\s*=\s*\{[\s\S]*?\}\s*\n)(\s*items\.append\(\s*item\s*\))"
if re.search(pattern, s):
    s = re.sub(pattern, r"\1item = _backfill_feed_item(item)\n\2", s, count=1)

# 3) Patch dict-literal append: items.append({...})
pattern2 = r"items\.append\(\s*(\{[\s\S]*?\})\s*\)"
if re.search(pattern2, s):
    # only change the first occurrence to avoid over-touching
    s = re.sub(pattern2, r"items.append(_backfill_feed_item(\1))", s, count=1)

# 4) As a safety net, patch the response return if it uses {"items": items, ...}
# This won't hurt if already backfilled.
s = re.sub(
    r'(return\s+\{\s*("items"|\'items\')\s*:\s*)items(\s*,)',
    r"\1[_backfill_feed_item(x) for x in items]\3",
    s,
    count=1
)

p.write_text(s, encoding="utf-8")
print("✅ Patched api/feed.py to apply backfill per-item (append-time + return-time)")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
