#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-http://localhost:18080}"
SERVICE_NAME="${SERVICE_NAME:-frostgate-core}"
CONTAINER_NAME="${CONTAINER_NAME:-frostgate-core-frostgate-core-1}"
READY_TIMEOUT_SECS="${READY_TIMEOUT_SECS:-45}"
RETRY_SECS="${RETRY_SECS:-15}"

die() { echo "ERROR: $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

dump_diag() {
  echo "---- docker compose ps ----" >&2
  docker compose ps >&2 || true
  echo "---- container inspect ----" >&2
  docker inspect -f 'name={{.Name}} state={{.State.Status}} restart={{.RestartCount}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_NAME" >&2 2>/dev/null || true
  echo "---- frostgate-core logs (tail 200) ----" >&2
  docker compose logs --tail=200 "$SERVICE_NAME" >&2 || true
}

# Load .env into shell if present (for local runs)
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Prefer KEY already exported, else fall back to env vars
RAW_KEY="${KEY:-${FG_SMOKE_AGENT_KEY:-${FG_AGENT_KEY:-}}}"
RAW_KEY="${RAW_KEY%%|*}" # strip "|scopes" if present

[[ -n "${RAW_KEY}" ]] || die "No API key found. Set KEY or FG_AGENT_KEY (raw key only)."

have curl || die "curl missing"
have jq   || die "jq missing"

echo "[0/3] Wait for /health/ready (timeout=${READY_TIMEOUT_SECS}s)"
deadline=$((SECONDS + READY_TIMEOUT_SECS))
until curl -fsS "$BASE/health/ready" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    dump_diag
    die "Service never became ready at $BASE within ${READY_TIMEOUT_SECS}s"
  fi
  sleep 0.5
done

req_retry_deadline=$((SECONDS + RETRY_SECS))

request() {
  # request <curl args...>
  # Retry transient failures for a short window (during restart flaps)
  while true; do
    if curl -fsS "$@"; then
      return 0
    fi
    if (( SECONDS >= req_retry_deadline )); then
      return 1
    fi
    sleep 0.4
  done
}

# --- Backwards compatible export ---
# Some routers import require_scopes; keep it stable.
def require_scopes(scopes: list[str] | set[str] | tuple[str, ...]):
    return require_any_scopes(scopes)  # <-- rename to your real function


TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "[1/3] POST /ingest"
if ! request "$BASE/ingest" \
  -H "X-API-Key: $RAW_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\":\"t1\",
    \"source\":\"agent\",
    \"timestamp\":\"$TS\",
    \"event_type\":\"auth.bruteforce\",
    \"event\":{\"src_ip\":\"10.0.0.77\",\"username\":\"jason\",\"attempts\":7}
  }" >/dev/null
then
  dump_diag
  die "/ingest failed (service flapping or server error)."
fi

echo "[2/3] GET /decisions?limit=1"
ID="$(curl -fsS "$BASE/decisions?limit=1" -H "X-API-Key: $RAW_KEY" | jq -r '.items[0].id // empty')"
[[ -n "$ID" ]] || { dump_diag; die "No decision ID returned. Decision logging broken."; }

echo "[3/3] GET /decisions/$ID and assert"
if ! curl -fsS "$BASE/decisions/$ID" -H "X-API-Key: $RAW_KEY" | jq -e '
  .tenant_id == "t1"
  and .request.tenant_id == "t1"
  and .event_type == "auth.bruteforce"
  and (.threat_level == "high" or .threat_level == "medium" or .threat_level == "low")
' >/dev/null
then
  dump_diag
  die "Decision detail assertion failed."
fi

echo "OK: ingest -> decisions -> decision detail (id=$ID)"
