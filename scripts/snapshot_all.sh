#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="artifacts/snapshots/${TS}"
mkdir -p "$OUT_DIR"

echo "Generating full snapshot bundle -> ${OUT_DIR}"

# 1) Canonical context snapshot (should create artifacts/FG_CONTEXT_LATEST.md + snapshot folder)
if [[ -x "scripts/snapshot_context.sh" ]]; then
  bash ./scripts/snapshot_context.sh
else
  echo "ERROR: scripts/snapshot_context.sh not found/executable"
  exit 1
fi

# 2) Extra “everything I might possibly need”
git status --porcelain=v1 > "${OUT_DIR}/git_status.txt" || true
git --no-pager log -n 20 --oneline > "${OUT_DIR}/git_log_20.txt" || true
git diff > "${OUT_DIR}/git_diff_working.patch" || true
git diff --staged > "${OUT_DIR}/git_diff_staged.patch" || true

# Make targets
make -qp 2>/dev/null | awk -F: '/^[a-zA-Z0-9_.-]+:([^=]|$)/{print $1}' \
  | sort -u > "${OUT_DIR}/make_targets.txt" || true

# Tree L3 (prefer tree)
if command -v tree >/dev/null 2>&1; then
  tree -L 3 -a -I '.venv|__pycache__|.git|node_modules|artifacts|state' > "${OUT_DIR}/tree_L3.txt"
else
  find . -maxdepth 3 -print | sed 's|^\./||' | sort > "${OUT_DIR}/tree_L3.txt"
fi

# Quick env scan for FG_ usage (if ripgrep exists)
if command -v rg >/dev/null 2>&1; then
  rg -n "FG_[A-Z0-9_]+" -S . \
    --glob '!.venv/**' --glob '!.git/**' --glob '!artifacts/**' --glob '!state/**' \
    > "${OUT_DIR}/fg_env_usages.txt" || true
fi

# Record where “latest” points
echo "${TS}" > "${OUT_DIR}/LATEST_POINTER.txt"

# Bundle it (optional but nice)
tar -czf "artifacts/snapshots/${TS}.tgz" -C "artifacts/snapshots" "${TS}"

echo "✅ Snapshot complete:"
echo " - ${OUT_DIR}"
echo " - artifacts/snapshots/${TS}.tgz"
echo " - artifacts/FG_CONTEXT_LATEST.md (latest pointer)"
