#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"
KEY="${FG_API_KEY:-supersecret}"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# 1) Get cookie (UI token endpoint sets HttpOnly cookie)
curl -sS -D /dev/null -o /dev/null -c "$tmp" "${BASE}/ui/token?api_key=${KEY}"

# 2) Cookie should authorize /feed/live (200)
code="$(curl -sS -o /dev/null -w '%{http_code}' -b "$tmp" "${BASE}/feed/live?limit=1")"
if [[ "$code" != "200" ]]; then
  echo "❌ /feed/live expected 200, got ${code}"
  exit 1
fi

# 3) SSE endpoint should advertise event-stream (HEADERS ONLY)
# Use HEAD to avoid reading the streaming body at all (no curl 18 from early abort)
hdr="$(
  curl -sS --max-time 2 --head -D - -o /dev/null -b "$tmp" \
    "${BASE}/feed/stream?limit=1&interval=1.0" \
  || true
)"

echo "$hdr" | grep -qi '^content-type: text/event-stream' || {
  echo "❌ /feed/stream missing content-type: text/event-stream"
  echo "$hdr"
  exit 1
}

echo "✅ smoke_ui_sse ok"
