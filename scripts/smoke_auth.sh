#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${FG_BASE_URL:-${BASE_URL:-http://127.0.0.1:8000}}"
API_KEY="${FG_API_KEY:-${API_KEY:-supersecret}}"
CJ="${CJ:-/tmp/fg_smoke_cj.txt}"
TMPDIR="${TMPDIR:-/tmp}"

# normalize
BASE_URL="${BASE_URL%/}"

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

fail() { red "❌ $*"; exit 1; }
ok()   { green "✅ $*"; }

http_code() {
  local url="$1"; shift
  curl -sS -o /dev/null -w '%{http_code}' "$@" "$url"
}

# Prints response headers only (CR-stripped). URL must be last.
resp_headers() {
  local url="$1"; shift
  curl -sS -D - -o /dev/null "$@" "$url" | tr -d '\r'
}

# Extract a header value (case-insensitive) from resp_headers output.
header_value() {
  local url="$1"
  local header="$2"
  shift 2
  resp_headers "$url" "$@" \
    | awk -v h="$header" '
        BEGIN { h=tolower(h) }
        /^[[:space:]]*$/ { next }
        {
          key=$1
          sub(/:$/, "", key)
          if (tolower(key) == h) {
            sub(/^[^:]*:[[:space:]]*/, "", $0)
            print $0
            exit
          }
        }
      '
}

assert_code() {
  local got="$1"
  local want="$2"
  local msg="$3"
  [[ "$got" == "$want" ]] || fail "$msg expected HTTP $want, got $got"
  ok "$msg -> HTTP $want"
}

echo "== FrostGate smoke auth check =="
echo "BASE_URL=$BASE_URL"

# health
c="$(http_code "$BASE_URL/health")"
assert_code "$c" "200" "/health"

# get cookie
echo "-- getting cookie via /ui/token"
rm -f "$CJ" || true
c="$(http_code "$BASE_URL/ui/token?api_key=$API_KEY" -c "$CJ")"
assert_code "$c" "200" "/ui/token (valid api_key)"
grep -q 'fg_api_key' "$CJ" || fail "cookie jar missing fg_api_key"
ok "cookie jar captured fg_api_key"

# /ui/feed no cookie => 401 + x-fg-authgate present
echo "-- /ui/feed without cookie"
c="$(http_code "$BASE_URL/ui/feed")"
assert_code "$c" "401" "/ui/feed (no cookie)"
x="$(header_value "$BASE_URL/ui/feed" "x-fg-authgate" || true)"
[[ -n "$x" ]] || fail "/ui/feed missing x-fg-authgate"
ok "/ui/feed includes x-fg-authgate"

# /ui/feed with cookie => 200 + content-type text/html
echo "-- /ui/feed with cookie"
c="$(http_code "$BASE_URL/ui/feed" -b "$CJ")"
assert_code "$c" "200" "/ui/feed (with cookie)"

ct="$(header_value "$BASE_URL/ui/feed" "content-type" -b "$CJ" || true)"
[[ -n "$ct" ]] || fail "/ui/feed with cookie missing Content-Type header"
echo "   content-type: $ct"
echo "$ct" | grep -qi '^text/html' || fail "/ui/feed with cookie expected text/html Content-Type, got: $ct"
ok "/ui/feed content-type looks html"

# SSE with cookie => must emit at least one data: line
echo "-- SSE /feed/stream with cookie"
tmp="$(mktemp "$TMPDIR/fg_sse.XXXXXX")"
# timeout is expected to stop the stream; ignore exit code
timeout 2s curl -sS -N -b "$CJ" \
  "$BASE_URL/feed/stream?limit=1&interval=0.2&q=&threat_level=" \
  >"$tmp" 2>/dev/null || true

grep -m1 '^data: ' "$tmp" >/dev/null || (sed -n '1,80p' "$tmp" >&2; rm -f "$tmp"; fail "SSE did not emit any data: within timeout")
ok "SSE emitted data:"
rm -f "$tmp" || true

green "== SMOKE OK =="
