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

feed = Path("api/feed.py")
text = feed.read_text(encoding="utf-8")

# 1) Locate the module that defines DecisionRecord
candidates = []
search_roots = [Path("api"), Path("backend")]
pat = re.compile(r'^\s*class\s+DecisionRecord\b', re.M)

for root in search_roots:
    if not root.exists():
        continue
    for p in root.rglob("*.py"):
        try:
            s = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if pat.search(s):
            candidates.append(p)

if not candidates:
    print("PATCH FAILED: Could not find a 'class DecisionRecord' definition under api/ or backend/.", file=sys.stderr)
    sys.exit(2)

# Prefer api/ over backend/, and prefer shorter paths (more likely canonical)
candidates.sort(key=lambda p: (0 if str(p).startswith("api/") else 1, len(str(p))))
target = candidates[0]

# Convert file path to python module path
mod = ".".join(target.with_suffix("").parts)
print(f"Found DecisionRecord in: {target}  -> module '{mod}'")

# 2) Patch feed.py: remove the broken in-function import and ensure a correct module-scope import exists.
# Remove any "from api.db import SessionLocal, DecisionRecord" (or similar) lines anywhere
text2 = re.sub(r'^\s*from\s+api\.db\s+import\s+SessionLocal\s*,\s*DecisionRecord\s*\n', "", text, flags=re.M)
text2 = re.sub(r'^\s*from\s+api\.db\s+import\s+DecisionRecord\s*\n', "", text2, flags=re.M)

# Remove in-function import block if present (exact line or with spacing)
text2 = re.sub(r'^\s*from\s+api\.db\s+import\s+SessionLocal\s*,\s*DecisionRecord\s*\n', "", text2, flags=re.M)
text2 = re.sub(r'^\s*from\s+api\.db\s+import\s+DecisionRecord\s*\n', "", text2, flags=re.M)

# Also remove any 'SessionLocal' import inside feed_live if your patch inserted it
text2 = re.sub(r'^\s*from\s+api\.db\s+import\s+SessionLocal\s*\n', "", text2, flags=re.M)

# Ensure we have a proper module-level import for DecisionRecord.
# Insert it after the first block of imports (after last "import ..." / "from ... import ..." line).
if re.search(rf'^\s*from\s+{re.escape(mod)}\s+import\s+DecisionRecord\s*$', text2, flags=re.M) is None:
    import_lines = list(re.finditer(r'^(from\s+\S+\s+import\s+.+|import\s+.+)\n', text2, flags=re.M))
    if not import_lines:
        # very unlikely, but handle it
        insert_at = 0
    else:
        insert_at = import_lines[-1].end()

    ins = f"from {mod} import DecisionRecord\n"
    text2 = text2[:insert_at] + ins + text2[insert_at:]
    print(f"Inserted module-scope import: {ins.strip()}")

# 3) Sanity: feed_live must NOT try to import DecisionRecord at runtime anymore.
# If you still have a stray "from api.db import ...DecisionRecord" anywhere, that's a fail.
if re.search(r'from\s+api\.db\s+import\s+.*DecisionRecord', text2):
    print("PATCH FAILED: A stray 'from api.db import ...DecisionRecord' still exists after patch.", file=sys.stderr)
    sys.exit(3)

feed.write_text(text2, encoding="utf-8")
print("✅ Patched api/feed.py DecisionRecord import")

# Compile check
import py_compile
py_compile.compile(str(feed), doraise=True)
print("✅ Compile OK: api/feed.py")
PY

