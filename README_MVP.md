# FrostGate Core — MVP

FrostGate Core is a defensive decision engine + forensic event log with a live feed.
It accepts security-relevant events, classifies them, logs decisions, and exposes a queryable feed and stats.

## What it does (today)
- `/health` reports service + auth mode
- `/defend` ingests an event and returns a decision (severity, rule hits, explainability)
- Decision logging persists to DB (SQLite by default in tests/dev)
- `/feed/live` shows recent decisions/events (auth-protected)
- `/stats` returns operational rollups (counts by severity, last 1h/24h, top rules)

## What it does NOT do (yet)
- Full EDR agent (process/kernel telemetry)
- Distributed message bus ingestion (NATS/Kafka)
- Multi-tenant isolation at the storage layer
- PDF report generation (planned)
- UI dashboard beyond basic endpoints (planned)

## Quickstart (dev)
```bash
# From repo root
source .venv/bin/activate
pytest -q

# Run API (example)
uvicorn api.main:app --reload --port 8000

Auth model (MVP)

Auth behavior is controlled per-app-instance via app.state.auth_enabled.

Test factory build_app(auth_enabled=...) must be reflected by /health.

Some endpoints are ALWAYS protected (ex: /feed/live).

Required headers (when enforced)

X-API-Key: supersecret (dev/test default)

Key endpoints

GET /health

POST /defend

GET /feed/live?limit=25

GET /stats?window=1h|24h

GET /decisions?limit=50 (if present)

Tester checklist (5 minutes)

Start the API and hit /health (confirm auth_enabled)

Run the demo harness: bash scripts/demo.sh

Confirm /feed/live is blocked without a key

Confirm /feed/live returns events with the key

Confirm /stats returns counts and top rules

Confirm decisions persist across restarts (DB file exists and grows)

Confirm tamper-evidence fields exist (hash/prev_hash) on records

Demo payload shape

POST /defend accepts JSON like:

{
  "event_type": "auth_bruteforce",
  "source_ip": "10.0.0.5",
  "username": "admin",
  "fail_count": 12
}

License / Disclaimer

This is an MVP for defensive monitoring and decisioning.
Do not use as your only security control.
Humans still click phishing links.
