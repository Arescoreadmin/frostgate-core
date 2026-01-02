#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-backend/tests/test_decision_diff_persistence.py}"
[[ -f "$FILE" ]] || { echo "ERROR: Missing file: $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("backend/tests/test_decision_diff_persistence.py")
s = p.read_text(encoding="utf-8")

# Replace the brittle "fields = set(changes)" block with a shape-safe extractor.
pattern = r"""
\s*#\s*Make\s*sure\s*it’s\s*meaningful:.*?\n
\s*fields\s*=\s*set\(changes\)\n
\s*assert\s*\(\{.*?\}\s*&\s*fields\).*?\n
"""

replacement = """
            # Make sure it’s meaningful: threat/decision/score change
            # changes may be ["field", ...] OR [{"field": "field", ...}, ...]
            fields = set()
            for c in changes:
                if isinstance(c, str):
                    fields.add(c)
                elif isinstance(c, dict):
                    # tolerate multiple schemas
                    f = c.get("field") or c.get("name") or c.get("key")
                    if f:
                        fields.add(str(f))

            assert ({"threat_level", "decision", "score"} & fields), f"diff not meaningful: fields={fields}, diff={diff}"
"""

s2, n = re.subn(pattern, replacement, s, flags=re.S | re.X)
if n != 1:
    raise SystemExit(f"PATCH FAILED: expected to replace 1 block, replaced {n}")

p.write_text(s2, encoding="utf-8")
print("✅ Patched decision diff field extraction to support dict-based changes")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK: $FILE"
