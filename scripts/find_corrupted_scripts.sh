#!/usr/bin/env bash
set -euo pipefail

echo "[*] Scanning scripts/ for obvious heredoc paste-corruption..."
rg -n "chmod \+x scripts/|patched and validated|poisoning your heredocs|guard\"|outer\(ingest_router\)" scripts || true
echo "[*] Done."
