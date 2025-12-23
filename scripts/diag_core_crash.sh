#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-frostgate-core}"
CONTAINER="frostgate-core-${SERVICE}-1"

echo "[*] docker compose ps"
docker compose ps || true
echo

echo "[*] container state"
docker inspect --format '{{.Name}} :: {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} exit={{.State.ExitCode}} restart={{.RestartCount}}' "$CONTAINER" 2>/dev/null || true
echo

echo "[*] last 220 log lines (filtered from first Traceback/Exception)"
docker compose logs --tail=220 "$SERVICE" 2>/dev/null | awk '
  /Traceback \(most recent call last\):/ {p=1}
  /ModuleNotFoundError:|ImportError:|SyntaxError:|Pydantic.*Error|TypeError:|AttributeError:|NameError:|ValueError:/ {p=1}
  p {print}
' | tail -n 220

echo
echo "[*] quick summary (first error-ish line near end):"
docker compose logs --tail=220 "$SERVICE" 2>/dev/null | grep -E "ModuleNotFoundError:|ImportError:|SyntaxError:|Pydantic.*Error|TypeError:|AttributeError:|NameError:|ValueError:" | tail -n 3 || true
