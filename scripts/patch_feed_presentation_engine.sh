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

# 1) Ensure helpers exist (idempotent insert near top, after existing imports)
if "_present_decision_record" not in s:
    insert_after = re.search(r"^from .*?\n(?:from .*?\n|import .*?\n)*\n", s, flags=re.M)
    if not insert_after:
        raise SystemExit("PATCH FAILED: couldn't locate import block")

    helper = r'''
def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

_THREAT_WEIGHT = {
    "none": 5.0,
    "low": 25.0,
    "medium": 55.0,
    "high": 85.0,
    "critical": 95.0,
}

def _action_from_score(threat_level: str | None, score: float, adv: float | None) -> str:
    tl = (threat_level or "none").lower()
    adv = adv or 0.0
    if score >= 85.0 or (tl in ("high", "critical") and adv >= 0.6):
        return "quarantine"
    if score >= 65.0:
        return "challenge"
    return "log_only"

def _title_summary(event_type: str | None, source: str | None, threat_level: str | None, action_taken: str, score: float) -> tuple[str, str]:
    et = (event_type or "event").lower()
    src = (source or "unknown").lower()
    tl = (threat_level or "none").lower()

    # Keep it short and “SOC-like”
    if et == "waf":
        title = f"WAF {action_taken.upper()} ({tl})"
        summary = f"{src} flagged request. Score {score:.0f}. Action: {action_taken}."
        return title, summary
    if et in ("edr", "process"):
        title = f"EDR {action_taken.upper()} ({tl})"
        summary = f"{src} detected suspicious process behavior. Score {score:.0f}. Action: {action_taken}."
        return title, summary
    if et in ("auth", "login"):
        title = f"AUTH {action_taken.upper()} ({tl})"
        summary = f"{src} detected abnormal authentication pattern. Score {score:.0f}. Action: {action_taken}."
        return title, summary

    title = f"{et.upper()} {action_taken.upper()} ({tl})"
    summary = f"{src} event. Score {score:.0f}. Action: {action_taken}."
    return title, summary

def _present_decision_record(r) -> dict:
    # Pull raw fields (DB schema is minimal)
    tl = getattr(r, "threat_level", None)
    anomaly = getattr(r, "anomaly_score", None)
    adv = getattr(r, "ai_adversarial_score", None)

    # Normalize numeric inputs
    anomaly = float(anomaly) if isinstance(anomaly, (int, float)) else 0.0
    adv = float(adv) if isinstance(adv, (int, float)) else 0.0

    # Compute score (0..100)
    tw = _THREAT_WEIGHT.get((tl or "none").lower(), 10.0)
    score = max(tw, anomaly * 100.0, adv * 100.0)
    score = _clamp(score, 0.0, 100.0)

    # Confidence (0..1). Simple mapping, but consistent.
    confidence = _clamp(0.5 + (score / 200.0), 0.0, 1.0)

    action_taken = _action_from_score(tl, score, adv)

    # Tier-ready enforcement knob (observe default)
    # In observe mode, we still emit the *recommended* action_taken for UI.
    # Future: enforcement engine reads same value and acts.
    # We intentionally do NOT mutate DB here.
    title, summary = _title_summary(getattr(r, "event_type", None), getattr(r, "source", None), tl, action_taken, score)

    return {
        "severity": _sev_from_threat(tl),
        "action_taken": action_taken,
        "title": title,
        "summary": summary,
        "confidence": confidence,
        "score": score,
    }
'''
    s = s[:insert_after.end()] + helper + s[insert_after.end():]

# 2) In feed_live loop, after diff/meta derive, ensure we apply presentation outputs.
# Find loop block and inject deterministic fields where you currently set title/summary/action/etc.
pat = r"(for r in rows:\n(?:[ \t].*\n)+?)(\n[ \t]*items\.append\()"
m = re.search(pat, s, flags=re.M)
if not m:
    raise SystemExit("PATCH FAILED: couldn't locate feed_live loop for injection")

loop = m.group(1)
if "_present_decision_record(r)" not in loop:
    inj = r'''
        # deterministic presentation from minimal DB fields
        pres = _present_decision_record(r)
'''
    loop2 = loop + inj
    s = s.replace(loop, loop2, 1)

# 3) Make sure the FeedItem dict uses pres[...] when setting fields.
# We don't know exact construction style; enforce via safe regex replacements on common keys.
# Replace any direct None-ish assignment with pres fallback.
repls = [
    (r'"severity"\s*:\s*[^,\n]+', '"severity": pres["severity"]'),
    (r'"action_taken"\s*:\s*[^,\n]+', '"action_taken": pres["action_taken"]'),
    (r'"title"\s*:\s*[^,\n]+', '"title": pres["title"]'),
    (r'"summary"\s*:\s*[^,\n]+', '"summary": pres["summary"]'),
    (r'"confidence"\s*:\s*[^,\n]+', '"confidence": pres["confidence"]'),
    (r'"score"\s*:\s*[^,\n]+', '"score": pres["score"]'),
]
# Only apply replacements inside the items.append(...) dict literal.
def patch_item_dict(txt: str) -> str:
    # capture the dict literal passed to FeedItem(...) or just dict(...)
    mm = re.search(r"items\.append\(\s*FeedItem\((?P<body>[\s\S]*?)\)\s*\)\s*", txt, flags=re.M)
    if not mm:
        mm = re.search(r"items\.append\(\s*(?P<body>\{[\s\S]*?\})\s*\)\s*", txt, flags=re.M)
    if not mm:
        return txt

    body = mm.group("body")
    body2 = body
    for a, b in repls:
        body2 = re.sub(a, b, body2, flags=re.M)
    return txt.replace(body, body2, 1)

s2 = patch_item_dict(s)
s = s2

p.write_text(s)
print("✅ Patched api/feed.py: deterministic presentation engine (score/confidence/action/title/summary)")
PY

python -m py_compile api/feed.py
echo "✅ Compile OK: api/feed.py"
