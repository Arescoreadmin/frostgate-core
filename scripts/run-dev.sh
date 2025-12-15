#!/usr/bin/env bash
set -euo pipefail

set -a
source .env.dev
set +a

exec scripts/dev-api.sh
