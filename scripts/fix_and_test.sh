#!/usr/bin/env bash
set -euo pipefail

scripts/audit_changes.sh .
python scripts/patch_repo.py

docker compose up -d --build
docker compose exec -T frostgate-core pytest -q
