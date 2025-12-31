#!/usr/bin/env bash
set -euo pipefail

f="api/defend.py"
test -f "$f" || { echo "❌ $f not found"; exit 1; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# Heuristic: look for the final response dict creation/return in defend()
# We will inject explanation_brief if it's missing.
# Strategy: if we see '"explain": explain,' inside the returned dict, add explanation_brief just before it.
if "explanation_brief" in s:
    print("ℹ️ explanation_brief already present somewhere; not patching blindly.")
    raise SystemExit(0)

pattern = r'(\n\s*["\']explain["\']\s*:\s*explain\s*,)'
m = re.search(pattern, s)
if not m:
    raise SystemExit("❌ Could not find the response dict key 'explain: explain,' to patch.")

# Insert explanation_brief derived from rules_triggered, or a safe fallback.
insertion = """
    "explanation_brief": (
        f"Suspicious behavior matched rule '{(explain.get('rules_triggered') or ['rule:unknown'])[0]}'."
        if isinstance(explain, dict) else "Decision computed."
    ),
"""

s = s[:m.start(1)] + "\n" + insertion + s[m.start(1):]

p.write_text(s)
print("✅ Patched api/defend.py to include top-level explanation_brief")
PY

python -m py_compile api/defend.py
