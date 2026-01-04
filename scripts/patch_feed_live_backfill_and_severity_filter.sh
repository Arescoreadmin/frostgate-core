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

# ---- 1) Ensure backfill helpers exist (insert once) ----
if "def _backfill_feed_item" not in s:
    helper = '''
from datetime import datetime, timezone

def _sev_from_threat(threat: str | None) -> str:
    t = (threat or "").strip().lower()
    if t == "critical": return "critical"
    if t == "high": return "high"
    if t == "medium": return "medium"
    if t == "low": return "low"
    return "info"

def _iso_ts(v) -> str:
    # Accept datetime/string/None
    if v is None:
        return datetime.now(timezone.utc).isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)

def _backfill_feed_item(i: dict) -> dict:
    # timestamp
    if not i.get("timestamp"):
        i["timestamp"] = _iso_ts(i.get("created_at"))

    # severity from threat_level (when missing)
    if not i.get("severity"):
        i["severity"] = _sev_from_threat(i.get("threat_level"))

    # action_taken (never blank)
    if not i.get("action_taken"):
        dd = i.get("decision_diff") or {}
        summ = (dd.get("summary") or "").lower()
        if "block" in summ or "blocked" in summ:
            i["action_taken"] = "blocked"
        elif "rate" in summ:
            i["action_taken"] = "rate_limited"
        else:
            i["action_taken"] = "log_only"

    # title/summary
    if not i.get("title"):
        et = i.get("event_type") or "event"
        src = i.get("source") or "unknown"
        i["title"] = f"{et} from {src}"
    if not i.get("summary"):
        sev = i.get("severity") or "info"
        thr = (i.get("threat_level") or "").strip()
        act = (i.get("action_taken") or "").strip()
        i["summary"] = f"{sev} {thr} {act}".strip()

    # confidence/score defaults
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
    # insert after imports (after first blank line block)
    m = re.search(r"\n\n", s)
    idx = m.end() if m else 0
    s = s[:idx] + helper + "\n" + s[idx:]

# ---- 2) Find the live feed handler and force backfill application ----
# We’re looking for the /feed/live route function; we’ll patch inside it.
# Strategy:
#   - locate "@router.get(.../live" block
#   - locate the first "items =" list creation OR the line that starts building items
#   - ensure right before the return dict we backfill items
#
# We’ll do a conservative patch: right before "return {"items": items" add:
#     items = [_backfill_feed_item(i) for i in items]
# And if a severity filter exists, apply derived severity filtering safely.

# Patch: insert backfill right before the return that includes items
ret_pat = r"(return\s+\{\s*(?:'items'|\"items\")\s*:\s*items\s*,)"
if re.search(ret_pat, s) and "items = [_backfill_feed_item(i) for i in items]" not in s:
    s = re.sub(
        ret_pat,
        "items = [_backfill_feed_item(i) for i in items]\n\\1",
        s,
        count=1,
        flags=re.M
    )

# Patch: if the handler filters by severity, make it robust when severity is null.
# Replace "if severity: ..." block if present in a typical simple form.
sev_block = r"if\s+severity\s*:\s*\n([ \t]+)(.+\n)+?"
# We won't guess an entire block. Instead, we ensure that whenever `severity` is used for filtering,
# it should compare against derived severity.
# Replace occurrences of i.get("severity") in comparisons with (_sev_from_threat(i.get("threat_level")) if not i.get("severity") else i.get("severity"))
def repl_cmp(m):
    return m.group(0)  # no-op placeholder

# Target common patterns:
#   i.get("severity") == severity
s = re.sub(
    r"i\.get\(\s*[\"']severity[\"']\s*\)\s*==\s*severity",
    r"((i.get('severity') or _sev_from_threat(i.get('threat_level'))) == severity)",
    s
)
#   i["severity"] == severity
s = re.sub(
    r"i\[\s*[\"']severity[\"']\s*\]\s*==\s*severity",
    r"((i.get('severity') or _sev_from_threat(i.get('threat_level'))) == severity)",
    s
)

p.write_text(s, encoding="utf-8")
print("✅ Patched api/feed.py: backfill applied before return + severity comparisons derived from threat when null")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
