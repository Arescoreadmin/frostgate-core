#!/usr/bin/env bash
set -euo pipefail

cat > scripts/wait_core_ready.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://localhost:18080}"
SERVICE="${2:-frostgate-core}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-120}"

deadline=$(( $(date +%s) + MAX_WAIT_SECONDS ))

echo "[*] Waiting for container/service '$SERVICE' to be running/healthy..."
while true; do
  status="$(docker compose ps "$SERVICE" --format '{{.Status}}' 2>/dev/null || true)"
  if [[ -z "$status" ]]; then
    status="$(docker compose ps "$SERVICE" 2>/dev/null | tail -n +2 | tr -s ' ' || true)"
  fi

  echo "$status" | grep -qiE 'Up|running|healthy' && break

  if [[ $(date +%s) -ge $deadline ]]; then
    echo "ERROR: Timed out waiting for '$SERVICE' to start"
    docker compose ps
    exit 1
  fi
  sleep 0.3
done

echo "[*] Waiting for readiness: $URL/health/ready"
until curl -fsS "$URL/health/ready" >/dev/null; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "ERROR: Timed out waiting for readiness endpoint"
    exit 1
  fi
  sleep 0.3
done

echo "ready"
EOF

chmod +x scripts/wait_core_ready.sh

if rg -n "chmod \+x scripts/wait_core_ready\.shfor|for readiness endpoint\"\"" scripts/wait_core_ready.sh >/dev/null 2>&1; then
  echo "ERROR: wait script still contains junk text. Something is poisoning your heredocs."
  exit 1
fi

echo "[+] scripts/wait_core_ready.sh patched and validated."
