#!/usr/bin/env bash
set -Eeuo pipefail

# ----------------------------
# FrostGate Core Demo Runner
# ----------------------------
PROJECT_NAME="FrostGate Core"
LOG_DIR="demo"
TS="$(date +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_DIR}/demo_run_${TS}.log"

# Pause mode:
#   PAUSE_MODE=enter  -> press Enter between steps
#   PAUSE_MODE=seconds -> auto wait (PAUSE_SECONDS)
PAUSE_MODE="${PAUSE_MODE:-enter}"
PAUSE_SECONDS="${PAUSE_SECONDS:-2}"

# CTA
CTA_EMAIL="${CTA_EMAIL:-you@yourdomain.com}"
CTA_URL="${CTA_URL:-https://yourdomain.com}"
CTA_TEXT="${CTA_TEXT:-Want a pilot install, pricing, or enterprise hardening?}"

mkdir -p "$LOG_DIR"

# Pretty printing
hr() { printf "\n%s\n" "------------------------------------------------------------"; }
say() { printf "\n▶ %s\n" "$*"; }
ok()  { printf "✅ %s\n" "$*"; }
warn(){ printf "⚠️  %s\n" "$*"; }

pause() {
  case "$PAUSE_MODE" in
    enter)
      read -r -p "⏸  Press Enter to continue..." _
      ;;
    seconds)
      sleep "$PAUSE_SECONDS"
      ;;
    none)
      ;;
    *)
      warn "Unknown PAUSE_MODE='$PAUSE_MODE' (use enter|seconds|none). Defaulting to enter."
      read -r -p "⏸  Press Enter to continue..." _
      ;;
  esac
}

run() {
  # Run a command, tee output to log, fail hard if it fails.
  # shellcheck disable=SC2145
  say "$*"
  # use bash -lc to keep behavior consistent (PATH, env, etc.)
  bash -lc "$*" 2>&1 | tee -a "$LOG_FILE"
}

on_err() {
  hr
  warn "Demo failed. Check log: $LOG_FILE"
  exit 1
}
trap on_err ERR

banner() {
  hr
  printf "%s\n" "$PROJECT_NAME Demo Runner"
  printf "Log: %s\n" "$LOG_FILE"
  printf "Pause mode: %s\n" "$PAUSE_MODE"
  hr
}

cta() {
  hr
  printf "🎯 DEMO COMPLETE\n\n"
  printf "%s\n" "$CTA_TEXT"
  printf "Email: %s\n" "$CTA_EMAIL"
  printf "Site:  %s\n" "$CTA_URL"
  printf "\nLog saved to: %s\n" "$LOG_FILE"
  hr
}

main() {
  banner

  # Optional: activate venv if present and not already active
  if [[ -z "${VIRTUAL_ENV:-}" && -f ".venv/bin/activate" ]]; then
    run "source .venv/bin/activate && python -V"
  else
    run "python -V"
  fi
  pause

  hr
  say "1) Sanity: compile key modules"
  run "python -m py_compile api/main.py api/auth_scopes.py api/feed.py api/defend.py"
  ok "Compilation OK"
  pause

  hr
  say "2) Run fast smoke tests (auth + feed)"
  run "pytest -q tests/test_auth.py::test_health_reflects_auth_enabled"
  run "pytest -q tests/test_feed_endpoint.py::test_feed_live_requires_auth"
  ok "Smoke tests OK"
  pause

  hr
  say "3) Full test suite"
  run "pytest -q"
  ok "All tests passed"
  pause

  hr
 hr
say "4) Show key endpoints exist (route listing)"

cat > /tmp/fg_routes_demo.py <<'PY'
from api.main import app
from fastapi.routing import APIRoute

want = {"/health","/status","/v1/status","/defend","/v1/defend","/feed/live","/stats"}
for r in app.routes:
    if isinstance(r, APIRoute) and r.path in want:
        methods = ",".join(sorted(r.methods))
        print(f"{r.path:12} {methods}")
PY

run "python /tmp/fg_routes_demo.py"
run "rm -f /tmp/fg_routes_demo.py"
pause

  hr
  say "5) Optional: show last 5 decisions in DB (if your DB model/table exists)"
  # This won't break the demo if db isn't configured; it will just display a message.
  bash -lc "python - <<'PY'\nimport os\nfrom sqlalchemy import create_engine, text\nurl = os.getenv('FG_DB_URL')\nif not url:\n    print('FG_DB_URL not set; skipping DB preview')\n    raise SystemExit(0)\n\ntry:\n    eng = create_engine(url)\n    with eng.connect() as c:\n        # best-effort table name guess. Adjust if needed.\n        for tbl in ('decision_records','decisions','decisionrecord','decision_record'):\n            try:\n                rows = c.execute(text(f\"select id, event_id, event_type, threat_level, created_at from {tbl} order by id desc limit 5\")).fetchall()\n                print(f\"Table: {tbl}\")\n                for r in rows:\n                    print(r)\n                break\n            except Exception:\n                continue\n        else:\n            print('Could not find a decisions table; skipping')\nexcept Exception as e:\n    print('DB preview skipped:', repr(e))\nPY" 2>&1 | tee -a "$LOG_FILE"
  pause

  cta
}

main "$@"
