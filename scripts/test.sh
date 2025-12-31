#!/usr/bin/env bash
set -euo pipefail
if [[ ! -x .venv/bin/python ]]; then
  echo "❌ .venv not found. Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python -m pytest -q "$@"
