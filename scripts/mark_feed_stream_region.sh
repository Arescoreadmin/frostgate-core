#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

BEGIN = "# === STREAM BEGIN (do not patch with regex) ==="
END   = "# === STREAM END ==="

if BEGIN in s and END in s:
    print("✅ Stream region already marked")
    raise SystemExit(0)

# Find the stream block (HEAD and GET). We assume your file has both decorators now.
# We'll wrap from the first @router.head("/stream") through the end of the GET /stream function.
m_head = re.search(r'^[ \t]*@router\.head\(\s*"/stream"\s*\)\s*$', s, flags=re.M)
m_get  = re.search(r'^[ \t]*@router\.get\(\s*"/stream"\s*\)\s*$', s, flags=re.M)

if not m_head or not m_get:
    raise SystemExit("❌ Could not locate both @router.head('/stream') and @router.get('/stream') in api/feed.py")

start = m_head.start()

# Find end of GET /stream function by locating the next decorator at column 0 (or EOF)
# starting AFTER the GET decorator.
after_get = m_get.end()
m_next = re.search(r'(?m)^[ \t]*@router\.', s[after_get:])
end = after_get + (m_next.start() if m_next else len(s[after_get:]))

# Expand end to include any trailing blank lines
while end < len(s) and s[end] in ("\n", "\r"):
    end += 1

block = s[start:end]
marked = f"{BEGIN}\n{block}\n{END}\n"

s2 = s[:start] + marked + s[end:]
p.write_text(s2)

print("✅ Marked /stream region with BEGIN/END guards")
PY

python -m py_compile api/feed.py
echo "✅ py_compile ok"
