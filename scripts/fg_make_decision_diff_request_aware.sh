#!/usr/bin/env bash
set -euo pipefail

test -f api/defend.py || { echo "❌ api/defend.py not found"; exit 1; }

echo "==> Making decision_diff_json request-aware (request + response basis)"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# We will inject a "diff_basis" dict right after request_payload/response_payload are built,
# then ensure decision_diff uses that basis instead of raw response_json.

# 1) Inject diff_basis after response_payload assignment
needle = r"response_payload\s*=\s*_safe_dump\(decision\)\s*\n"
if not re.search(needle, s):
    raise SystemExit("❌ Could not find response_payload = _safe_dump(decision) in api/defend.py")

inject = """
response_payload = _safe_dump(decision)

    # diff_basis is what we actually diff across decisions.
    # It includes request context so drift is visible even when gating stays "allow".
    diff_basis = {
        "request": request_payload,
        "decision": {
            "threat_level": getattr(decision, "threat_level", None),
            "ai_adversarial_score": float(getattr(decision, "ai_adversarial_score", 0.0) or 0.0),
            "pq_fallback": bool(getattr(decision, "pq_fallback", False)),
            "explain_summary": getattr(getattr(decision, "explain", None), "summary", None),
            "rules_triggered": list(getattr(getattr(decision, "explain", None), "rules_triggered", []) or []),
            "anomaly_score": float(getattr(getattr(decision, "explain", None), "anomaly_score", 0.0) or 0.0),
        },
    }

"""
s = re.sub(needle, inject, s, count=1)

# 2) Replace any place where decision_diff is computed using response_payload with diff_basis.
# We look for compute_decision_diff(prev_..., response_payload) OR compute_decision_diff(prev_..., resp_value/response_payload)
# and swap second arg to diff_basis.
s2 = re.sub(
    r"(compute_decision_diff\(\s*[^,]+,\s*)(response_payload|resp_value)\s*(\)\s*)",
    r"\1diff_basis\3",
    s
)

# 3) If code sets record_kwargs["decision_diff_json"] = ... but passes response_payload directly, patch that too.
s2 = re.sub(
    r'("decision_diff_json"\s*:\s*)(compute_decision_diff\(\s*[^,]+,\s*)(response_payload|resp_value)(\s*\))',
    r'\1\2diff_basis\4',
    s2
)

if s2 == s:
    # still ok, maybe your code already differs or uses diff_basis elsewhere
    print("ℹ️ No compute_decision_diff(response_payload/resp_value) patterns found; diff_basis still injected.")
else:
    print("✅ Patched compute_decision_diff to use diff_basis")

p.write_text(s2)
print("✅ Updated api/defend.py")
PY

python -m py_compile api/defend.py
echo "✅ api/defend.py compiles"
./scripts/test.sh
