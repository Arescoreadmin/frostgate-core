#!/usr/bin/env bash
set -euo pipefail

# ---------- config ----------
SERVICE="frostgate-core"
HOST_BASE_URL="${FG_SMOKE_BASE_URL:-http://localhost:18080}"
TIMEOUT_SECS="${FG_SMOKE_TIMEOUT_SECS:-120}"
SLEEP_SECS="${FG_SMOKE_POLL_SECS:-0.5}"

# If you want raw request/response validation, set FG_SMOKE_INCLUDE_RAW=true
INCLUDE_RAW="${FG_SMOKE_INCLUDE_RAW:-false}"

# ---------- helpers ----------
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { printf "%s %s\n" "$(ts)" "$*"; }

die() {
  log "❌ $*"
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

print_debug() {
  log "---- docker compose ps ----"
  docker compose ps || true
  log "---- frostgate-core logs (tail 250) ----"
  docker compose logs --no-log-prefix --tail=250 "$SERVICE" || true
  log "---- postgres logs (tail 120) ----"
  docker compose logs --no-log-prefix --tail=120 postgres || true
  log "---- redis logs (tail 120) ----"
  docker compose logs --no-log-prefix --tail=120 redis || true
}

http_code() {
  # usage: http_code URL [extra curl args...]
  local url="$1"; shift || true
  curl -sS -o /dev/null -w "%{http_code}" "$url" "$@" || echo "000"
}

curl_json() {
  # usage: curl_json URL [extra curl args...]
  local url="$1"; shift || true
  curl -fsS "$url" "$@"
}

wait_for_compose_service() {
  local deadline=$((SECONDS + TIMEOUT_SECS))

  log "[1] locating container id for service=$SERVICE ..."
  local cid
  cid="$(docker compose ps -q "$SERVICE" 2>/dev/null || true)"
  if [[ -z "${cid}" ]]; then
    docker compose ps || true
    die "container id is empty. Did you run: docker compose up -d --build ?"
  fi
  log "ok cid=$cid"

  log "[2] waiting for container health=healthy (timeout=${TIMEOUT_SECS}s)..."
  while true; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")"

    if [[ "$status" == "healthy" ]]; then
      log "ok healthy"
      break
    fi

    if [[ "$status" == "unhealthy" ]]; then
      log "❌ container is unhealthy"
      docker inspect -f '{{json .State.Health}}' "$cid" | jq . || true
      print_debug
      exit 1
    fi

    if (( SECONDS >= deadline )); then
      log "❌ timed out waiting for healthy (status=$status)"
      docker inspect -f '{{json .State.Health}}' "$cid" | jq . || true
      print_debug
      exit 1
    fi

    sleep "$SLEEP_SECS"
  done
}

require_admin_key() {
  if [[ -z "${ADMIN_KEY:-}" ]]; then
    if [[ -f ".env" ]]; then
      ADMIN_KEY="$(grep -E '^FG_ADMIN_KEY=' .env | cut -d= -f2- || true)"
    fi
  fi
  [[ -n "${ADMIN_KEY:-}" ]] || die "ADMIN_KEY not set and FG_ADMIN_KEY not found in .env"
}

post_defend() {
  # usage: post_defend timestamp_iso
  local ts_iso="$1"
  local payload
  payload="$(python - <<PY
import json
print(json.dumps({
  "tenant_id":"local",
  "source":"smoke",
  "timestamp":"$ts_iso",
  "payload":{"event_type":"auth.bruteforce","source_ip":"1.2.3.4","fail_count":12}
}))
PY
)"
  curl -fsS "${HOST_BASE_URL}/defend" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${ADMIN_KEY}" \
    -d "$payload" >/dev/null
}

# ---------- main ----------
need_cmd docker
need_cmd curl
need_cmd jq
need_cmd python

wait_for_compose_service
require_admin_key

log "[3] health endpoints..."
curl -fsS "${HOST_BASE_URL}/health/live" >/dev/null || { print_debug; die "health/live failed"; }
curl -fsS "${HOST_BASE_URL}/health/ready" >/dev/null || { print_debug; die "health/ready failed"; }
log "ok"

log "[4] unauthorized must NOT be 200..."
code="$(http_code "${HOST_BASE_URL}/decisions")"
if [[ "$code" == "200" ]]; then
  print_debug
  die "unauthorized /decisions returned 200 (auth is broken)"
fi
log "ok (unauthorized code=$code)"

log "[5] generate a defend decision..."
NOW="$(python - <<'PY'
import datetime
print(datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00","Z"))
PY
)"
post_defend "$NOW"
log "ok"

log "[6] verify decisions list returns at least 1 item..."
resp="$(curl_json "${HOST_BASE_URL}/decisions?limit=10&include_raw=${INCLUDE_RAW}" -H "X-API-Key: ${ADMIN_KEY}")" || {
  print_debug
  die "/decisions request failed"
}

# Validate response shape defensively
echo "$resp" | jq -e 'has("items") and ( .items | type == "array") and has("total")' >/dev/null || {
  log "response was:"
  echo "$resp" | jq . || true
  print_debug
  die "/decisions response shape invalid"
}

total="$(echo "$resp" | jq -r '.total')"
count="$(echo "$resp" | jq -r '.items | length')"

if [[ "$count" -lt 1 || "$total" -lt 1 ]]; then
  log "response was:"
  echo "$resp" | jq . || true
  print_debug
  die "no decisions returned (expected >= 1)"
fi
log "ok total=$total returned=$count"

log "[7] verify newest decision fields..."
echo "$resp" | jq -e '
  .items[0].tenant_id == "local" and
  .items[0].source == "smoke" and
  .items[0].event_type == "auth.bruteforce" and
  .items[0].threat_level == "high" and
  (.items[0].rules_triggered | type == "array") and
  (.items[0].rules_triggered | length >= 1)
' >/dev/null || {
  log "response was:"
  echo "$resp" | jq . || true
  print_debug
  die "newest decision missing expected fields/values"
}
log "ok"

if [[ "${INCLUDE_RAW}" == "true" ]]; then
  log "[8] include_raw=true: verify request/response are present..."
  echo "$resp" | jq -e '.items[0].request != null and .items[0].response != null' >/dev/null || {
    log "response was:"
    echo "$resp" | jq . || true
    print_debug
    die "include_raw=true but request/response are null"
  }
  log "ok"
else
  log "[8] include_raw=false: request/response should be null (expected)…"
  echo "$resp" | jq -e '.items[0].request == null and .items[0].response == null' >/dev/null || {
    log "response was:"
    echo "$resp" | jq . || true
    print_debug
    die "include_raw=false but request/response are not null (unexpected)"
  }
  log "ok"
fi

log "✅ smoke test passed"
