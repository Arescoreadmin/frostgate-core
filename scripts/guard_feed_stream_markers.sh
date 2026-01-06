set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

f="api/feed.py"

begin='# === STREAM BEGIN (do not patch with regex) ==='
end='# === STREAM END ==='

if ! grep -qF "$begin" "$f"; then
  echo "❌ Missing stream marker BEGIN in $f"
  echo "   expected line: $begin"
  exit 1
fi

if ! grep -qF "$end" "$f"; then
  echo "❌ Missing stream marker END in $f"
  echo "   expected line: $end"
  exit 1
fi

echo "✅ stream markers present in $f"