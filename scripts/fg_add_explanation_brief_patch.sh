#!/usr/bin/env bash
set -euo pipefail

# Safety rails
if [[ ! -d api ]]; then
  echo "❌ Run this from repo root (expected ./api)."
  exit 1
fi

echo "==> [1/6] Adding brief-explanation generator (api/explain_brief.py)"
mkdir -p api
cat > api/explain_brief.py <<'PY'
from __future__ import annotations

from typing import Any, Dict, List, Optional

def build_explanation_brief(
    event_type: str,
    triggered_rules: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Deterministic, testable 1-liner explanation for humans.
    No LLMs, no fluff, no internal scoring jargon.
    """
    metadata = metadata or {}
    source_ip = metadata.get("source_ip") or metadata.get("ip") or "an unknown source"
    username = metadata.get("username") or metadata.get("user") or "an unknown user"

    if not triggered_rules:
        return "No threat rules triggered for this event."

    # Pick primary rule: first triggered (keep deterministic)
    primary = triggered_rules[0]

    templates = {
        "auth_bruteforce": f"Repeated failed logins from {source_ip} triggered the brute-force rule.",
        "brute_force": f"Repeated failed logins from {source_ip} triggered the brute-force rule.",
        "rate_limit": f"High request rate from {source_ip} triggered the rate-limit rule.",
        "suspicious_login": f"Suspicious login activity for {username} from {source_ip} triggered a login anomaly rule.",
    }

    return templates.get(primary, f"Suspicious behavior matched rule '{primary}'.")
PY

echo "==> [2/6] Locating likely decision models"
# Try to find where a DecisionRecord / DecisionResponse / explanation exists.
CANDIDATES=$(rg -n "DecisionRecord|DecisionResponse|class Decision|explanation" api 2>/dev/null || true)
if [[ -z "${CANDIDATES}" ]]; then
  echo "❌ Couldn't find decision/explanation code under ./api."
  echo "   This script expects the decision flow lives under ./api."
  exit 1
fi

echo "==> [3/6] Patching api/defend.py (common path) if it exists"
if [[ -f api/defend.py ]]; then
  # Add import for brief generator if not present
  if ! rg -q "from api\.explain_brief import build_explanation_brief" api/defend.py; then
    python - <<'PY'
from pathlib import Path
p = Path("api/defend.py")
s = p.read_text()
# Insert near top-level imports
needle = "from fastapi import"
if needle in s and "build_explanation_brief" not in s:
    parts = s.split(needle, 1)
    s = parts[0] + "from api.explain_brief import build_explanation_brief\n" + needle + parts[1]
p.write_text(s)
PY
  fi

  # Ensure response models include explanation_brief if they're Pydantic
  python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# Try to add explanation_brief to any pydantic model that already has explanation
def add_field(block: str) -> str:
    if "explanation_brief" in block:
        return block
    # place it right above explanation or right after explanation
    if "explanation:" in block:
        block = re.sub(r"(\\n\\s*explanation:\\s*[^\\n]+)",
                       r"\\n    explanation_brief: str\\n\\1",
                       block, count=1)
    return block

pattern = r"class\\s+(\\w+\\b).*?\\n(\\s+explanation:\\s*[^\\n]+.*?)(?=\\nclass\\s+|\\Z)"
m = re.search(pattern, s, flags=re.S)
if m:
    # crude but effective: rewrite whole file by injecting field into the class body range
    cls_name = m.group(1)
    # find class block boundary
    cls_pat = re.compile(rf"(class\\s+{re.escape(cls_name)}\\b.*?:\\n)(.*?)(?=\\nclass\\s+|\\Z)", re.S)
    mm = cls_pat.search(s)
    if mm:
        head, body = mm.group(1), mm.group(2)
        new_body = add_field(body)
        s = s[:mm.start()] + head + new_body + s[mm.end():]

# Also attempt to inject explanation_brief into dict/json response payload creation
# Look for "explanation=" in a return dict or model constructor and add explanation_brief=...
if "build_explanation_brief" in s and "explanation_brief=" not in s:
    s = re.sub(r"(explanation\\s*=\\s*[^,\\n\\)]+)",
               r"\\1,\\n        explanation_brief=build_explanation_brief(event_type, triggered_rules, metadata)",
               s, count=1)

p.write_text(s)
PY

  echo "✅ Patched api/defend.py"
else
  echo "⚠️ api/defend.py not found. Skipping defend patch."
fi

echo "==> [4/6] Patching decision persistence model (SQLAlchemy) if found"
# We try common patterns: a SQLAlchemy model with 'explanation' column.
MODEL_FILE=$(rg -l "Column\\(.*explanation|explanation\\s*=\\s*Column" api 2>/dev/null | head -n 1 || true)
if [[ -n "${MODEL_FILE}" ]]; then
  python - <<PY
from pathlib import Path
import re

p = Path("${MODEL_FILE}")
s = p.read_text()

# Add explanation_brief column near explanation column if not present
if "explanation_brief" not in s:
    # Try SQLAlchemy Column style
    s2 = re.sub(r"(explanation\\s*=\\s*Column\\([^\\n]+\\)\\n)",
                r"\\1    explanation_brief = Column(String, nullable=False)\\n",
                s, count=1)
    if s2 != s:
        s = s2
    else:
        # Try pydantic field or dataclass style fallback: insert a field
        s = re.sub(r"(\\n\\s*explanation\\s*:\\s*str[^\\n]*\\n)",
                   r"\\n    explanation_brief: str\\1",
                   s, count=1)

p.write_text(s)
PY
  echo "✅ Patched model file: ${MODEL_FILE}"
else
  echo "⚠️ No obvious SQLAlchemy model file found containing explanation. Skipping DB schema patch."
  echo "   If you persist decisions, you MUST add explanation_brief to that schema manually."
fi

echo "==> [5/6] Adding safe default in decision creation (if we can find it)"
# Find the code that creates DecisionRecord(...) and patch it
CREATOR_FILE=$(rg -l "DecisionRecord\\(" api 2>/dev/null | head -n 1 || true)
if [[ -n "${CREATOR_FILE}" ]]; then
  python - <<PY
from pathlib import Path
import re

p = Path("${CREATOR_FILE}")
s = p.read_text()

if "explanation_brief" not in s and "DecisionRecord(" in s:
    # Add argument before explanation= if present
    s2 = re.sub(r"(DecisionRecord\\(\\s*.*?)(\\n\\s*explanation\\s*=)",
                r"\\1\\n    explanation_brief=build_explanation_brief(event_type, triggered_rules, metadata),\\2",
                s, count=1, flags=re.S)
    if s2 != s:
        s = s2

p.write_text(s)
PY
  echo "✅ Patched decision creator file: ${CREATOR_FILE}"
else
  echo "⚠️ Couldn't find DecisionRecord(...) constructor usage. Skipping creator patch."
fi

echo "==> [6/6] Quick sanity output (show where explanation_brief landed)"
rg -n "explanation_brief" api || true

echo
echo "✅ Patch applied. Next: run scripts/fg_add_explanation_brief_test.sh then your test target."
