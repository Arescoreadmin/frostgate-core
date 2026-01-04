#!/usr/bin/env bash
set -euo pipefail

# -----------------------------
# FrostGate uvicorn local runner
# -----------------------------
# Guarantees:
# - No "started" claim until /health responds
# - Optional /health/ready gating (configurable)
# - Stale pidfile cleanup
# - Refuses to start if port is owned by another process (unless FG_FORCE=1)
# - Stop waits for port to free (prevents ghost server failures)
# - "env" prints launch-relevant env values and best-effort process env
# - "openapi" uses a temp file (no SIGPIPE curl(23) nonsense)
#
# New knobs (because reality is messy):
# - FG_RESTART_IF_RUNNING=1  => start() will stop+start if already running (useful for e2e)
# - FG_READY_REQUIRED=1      => start/check requires /health/ready == 200 (default)
# - FG_READY_REQUIRED=0      => /health required, /ready informational only

HOST="${FG_HOST:-127.0.0.1}"
PORT="${FG_PORT:-8000}"

PIDFILE="${FG_PIDFILE:-artifacts/uvicorn.local.pid}"
LOGFILE="${FG_LOGFILE:-artifacts/uvicorn.local.log}"

APP="${FG_APP:-api.main:app}"
PY="${FG_PY:-.venv/bin/python}"

BASE_URL="${FG_BASE_URL:-http://${HOST}:${PORT}}"
READY_PATH="${FG_READY_PATH:-/health/ready}"
HEALTH_PATH="${FG_HEALTH_PATH:-/health}"

READY_REQUIRED="${FG_READY_REQUIRED:-1}"
RESTART_IF_RUNNING="${FG_RESTART_IF_RUNNING:-0}"

START_TIMEOUT_SEC="${FG_START_TIMEOUT_SEC:-10}"
READY_TIMEOUT_SEC="${FG_READY_TIMEOUT_SEC:-10}"
STOP_TIMEOUT_SEC="${FG_STOP_TIMEOUT_SEC:-8}"
POLL_INTERVAL_SEC="${FG_POLL_INTERVAL_SEC:-0.1}"

FORCE="${FG_FORCE:-0}"
STRICT="${FG_STRICT_START:-0}"

mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOGFILE")"

_now_ms() {
  "$PY" - <<'PY' 2>/dev/null || date +%s%3N
import time
print(int(time.time()*1000))
PY
}

_read_pidfile() {
  [[ -f "$PIDFILE" ]] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] || return 1
  echo "$pid"
}

_pid_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

_port_owner_pid() {
  ss -lptn "sport = :$PORT" 2>/dev/null \
    | awk -F'pid=' 'NR==2{print $2}' \
    | awk -F',' '{print $1}' \
    | tr -d '[:space:]' \
    | head -n 1
}

_wait_for_port_free() {
  local deadline_ms="$((_now_ms + STOP_TIMEOUT_SEC*1000))"
  while (( $(_now_ms) < deadline_ms )); do
    local opid
    opid="$(_port_owner_pid || true)"
    [[ -z "${opid:-}" ]] && return 0
    sleep "$POLL_INTERVAL_SEC"
  done
  return 1
}

_wait_for_health() {
  local deadline_ms="$((_now_ms + START_TIMEOUT_SEC*1000))"
  while (( $(_now_ms) < deadline_ms )); do
    if curl -fsS "${BASE_URL}${HEALTH_PATH}" >/dev/null 2>&1; then
      echo "✅ ${HEALTH_PATH} is up at ${BASE_URL}"
      return 0
    fi
    sleep "$POLL_INTERVAL_SEC"
  done

  echo "❌ Uvicorn did not become reachable at ${BASE_URL}${HEALTH_PATH} within ${START_TIMEOUT_SEC}s" >&2
  echo "---- last logs ----" >&2
  tail -n 200 "$LOGFILE" 2>/dev/null || true
  return 1
}

_ready_code() {
  curl -sS -o /dev/null -w "%{http_code}" "${BASE_URL}${READY_PATH}" || true
}

_ready_body() {
  curl -sS "${BASE_URL}${READY_PATH}" || true
}

_wait_for_ready_200() {
  local deadline_ms="$((_now_ms + READY_TIMEOUT_SEC*1000))"
  while (( $(_now_ms) < deadline_ms )); do
    local code
    code="$(_ready_code)"
    if [[ "$code" == "200" ]]; then
      echo "✅ ${READY_PATH} OK"
      return 0
    fi
    sleep "$POLL_INTERVAL_SEC"
  done

  echo "❌ Timed out waiting for ${READY_PATH} to return 200 (timeout ${READY_TIMEOUT_SEC}s)" >&2
  echo "---- ready body ----" >&2
  _ready_body >&2 || true
  echo "---- last logs ----" >&2
  tail -n 200 "$LOGFILE" 2>/dev/null || true
  return 1
}

_ready_check_informational() {
  local code
  code="$(_ready_code)"
  if [[ "$code" == "200" ]]; then
    echo "✅ ${READY_PATH} OK"
  elif [[ "$code" == "503" ]]; then
    echo "⚠️  ${READY_PATH} not ready yet (503) but server is up"
  else
    echo "⚠️  ${READY_PATH} returned ${code} (ignored)"
  fi
  return 0
}

_clean_stale_pidfile_if_needed() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(_read_pidfile || true)"
    if [[ -n "${pid:-}" ]] && ! _pid_alive "$pid"; then
      echo "⚠️  Stale pidfile: $PIDFILE points to dead pid=$pid. Removing."
      rm -f "$PIDFILE"
    fi
  fi
}

is_running() {
  _clean_stale_pidfile_if_needed
  local pid
  pid="$(_read_pidfile || true)"
  [[ -n "${pid:-}" ]] || return 1
  _pid_alive "$pid"
}

_precreate_sqlite_file() {
  # Your readiness check is "DB missing: <path>".
  # So we make sure the file exists before startup when FG_SQLITE_PATH is set.
  local p="${FG_SQLITE_PATH:-}"
  [[ -z "${p:-}" ]] && return 0
  # Normalize ./foo.db -> foo.db is fine; dirname works either way.
  local d
  d="$(dirname "$p")"
  mkdir -p "$d" 2>/dev/null || true
  touch "$p" 2>/dev/null || true
}

start() {
  _clean_stale_pidfile_if_needed

  if is_running; then
    if [[ "$STRICT" == "1" ]]; then
      echo "❌ uvicorn already running (pid=$(_read_pidfile)); strict mode refuses reuse" >&2
      exit 1
    fi

    if [[ "$RESTART_IF_RUNNING" == "1" ]]; then
      echo "⚠️  uvicorn already running (pid=$(_read_pidfile)); FG_RESTART_IF_RUNNING=1 so restarting to apply env"
      stop
    else
      echo "✅ uvicorn already running (pid=$(_read_pidfile))"
      exit 0
    fi
  fi

  local opid
  opid="$(_port_owner_pid || true)"
  if [[ -n "${opid:-}" ]]; then
    if [[ "$FORCE" == "1" ]]; then
      echo "⚠️  Port $PORT owned by pid=$opid. FG_FORCE=1 set, terminating."
      kill "$opid" 2>/dev/null || true
      if ! _wait_for_port_free; then
        echo "⚠️  Port still owned after grace. SIGKILL pid=$opid"
        kill -9 "$opid" 2>/dev/null || true
        _wait_for_port_free || true
      fi
    else
      echo "❌ Port $PORT is already in use by pid=$opid. Stop that process first (or set FG_FORCE=1)." >&2
      exit 1
    fi
  fi

  rm -f "$PIDFILE"

  _precreate_sqlite_file

  nohup "$PY" -m uvicorn "$APP" --host "$HOST" --port "$PORT" >"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"

  echo "✅ Started uvicorn (pid=$(_read_pidfile)) -> ${HOST}:${PORT}"

  _wait_for_health

  if [[ "$READY_REQUIRED" == "1" ]]; then
    _wait_for_ready_200
  else
    _ready_check_informational
  fi
}

stop() {
  _clean_stale_pidfile_if_needed

  if [[ ! -f "$PIDFILE" ]]; then
    echo "✅ uvicorn not running (no pidfile)"
    return 0
  fi

  local pid
  pid="$(_read_pidfile || true)"
  if [[ -z "${pid:-}" ]]; then
    rm -f "$PIDFILE"
    echo "✅ uvicorn not running (empty pidfile)"
    return 0
  fi

  if _pid_alive "$pid"; then
    kill "$pid" 2>/dev/null || true
  fi

  local deadline_ms="$((_now_ms + STOP_TIMEOUT_SEC*1000))"
  while (( $(_now_ms) < deadline_ms )); do
    if ! _pid_alive "$pid"; then
      break
    fi
    sleep "$POLL_INTERVAL_SEC"
  done

  if _pid_alive "$pid"; then
    echo "⚠️  pid=$pid still alive after ${STOP_TIMEOUT_SEC}s, SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$PIDFILE"

  if _wait_for_port_free; then
    echo "✅ Stopped uvicorn"
    return 0
  fi

  local opid
  opid="$(_port_owner_pid || true)"
  if [[ -n "${opid:-}" ]]; then
    echo "⚠️  Uvicorn stopped but port $PORT still owned by pid=$opid" >&2
  else
    echo "⚠️  Uvicorn stopped but port $PORT still appears busy" >&2
  fi
  return 0
}

restart() {
  stop
  start
}

status() {
  if is_running; then
    echo "✅ running pid=$(_read_pidfile)"
    exit 0
  fi
  echo "❌ not running"
  exit 1
}

logs() {
  tail -n "${1:-200}" "$LOGFILE"
}

openapi_check() {
  local tmp
  tmp="$(mktemp -t fg-openapi.XXXXXX.json)"
  trap 'rm -f "$tmp"' RETURN

  if ! curl -fsS "${BASE_URL}/openapi.json" -o "$tmp" >/dev/null 2>&1; then
    echo "❌ Cannot fetch ${BASE_URL}/openapi.json" >&2
    return 1
  fi

  "$PY" - <<PY
import json
spec=json.load(open("$tmp","r",encoding="utf-8"))
ok = "/dev/seed" in spec.get("paths",{})
print("✅ openapi includes /dev/seed" if ok else "❌ openapi missing /dev/seed")
raise SystemExit(0 if ok else 1)
PY
}

env_dump() {
  _clean_stale_pidfile_if_needed
  local pid
  pid="$(_read_pidfile || true)"
  if [[ -n "${pid:-}" ]]; then
    echo "PID=$pid"
  else
    echo "❌ No valid pidfile (or it was stale and got removed)."
    return 1
  fi

  cat <<EOF
FG_DEV_EVENTS_ENABLED=${FG_DEV_EVENTS_ENABLED:-}
FG_AUTH_ENABLED=${FG_AUTH_ENABLED:-}
FG_API_KEY=${FG_API_KEY:-}
FG_ENV=${FG_ENV:-}
FG_SERVICE=${FG_SERVICE:-}
FG_HOST=${HOST}
FG_PORT=${PORT}
FG_BASE_URL=${BASE_URL}
FG_STATE_DIR=${FG_STATE_DIR:-}
FG_SQLITE_PATH=${FG_SQLITE_PATH:-}
FG_ENFORCEMENT_MODE=${FG_ENFORCEMENT_MODE:-}
FG_STRICT_START=${STRICT}
FG_FORCE=${FORCE}
FG_READY_REQUIRED=${READY_REQUIRED}
FG_RESTART_IF_RUNNING=${RESTART_IF_RUNNING}
API_KEY=${API_KEY:-}
BASE_URL=${BASE_URL}
EOF

  if [[ -r "/proc/$pid/environ" ]]; then
    echo "--- /proc/$pid/environ (FG_* + BASE_URL/API_KEY) ---"
    tr '\0' '\n' <"/proc/$pid/environ" | rg '^(FG_|BASE_URL=|API_KEY=)' || true
  else
    echo "⚠️  Cannot read /proc/$pid/environ (permissions?)."
  fi
}

server_check() {
  if ! curl -fsS "${BASE_URL}${HEALTH_PATH}" >/dev/null; then
    echo "❌ ${HEALTH_PATH} not reachable at ${BASE_URL}" >&2
    echo "---- last logs ----" >&2
    tail -n 120 "$LOGFILE" 2>/dev/null || true
    return 1
  fi

  if [[ "$READY_REQUIRED" == "1" ]]; then
    local code
    code="$(_ready_code)"
    if [[ "$code" != "200" ]]; then
      echo "❌ ${READY_PATH} expected 200 but got ${code}" >&2
      echo "---- ready body ----" >&2
      _ready_body >&2 || true
      return 1
    fi
    echo "✅ ready"
  else
    _ready_check_informational
  fi
  return 0
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  logs) shift; logs "${1:-200}" ;;
  env) env_dump ;;
  check) server_check ;;
  openapi) openapi_check ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs [N]|env|check|openapi}"
    exit 2
    ;;
esac
