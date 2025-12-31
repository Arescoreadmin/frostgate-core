#!/usr/bin/env bash
set -euo pipefail

echo "==> Where is the decisions router defined?"
ls -la api/decisions.py 2>/dev/null || true
rg -n "router\s*=\s*APIRouter|prefix\s*=\s*\"/decisions\"|tags=\[\"decisions\"\]" api/decisions.py api -S || true

echo
echo "==> Is decisions router mounted in api/main.py?"
rg -n "decisions|include_router\(|from api\.decisions import|import .*decisions" api/main.py api -S || true

echo
echo "==> What routers are mounted today?"
rg -n "include_router\(" api/main.py -n || true
