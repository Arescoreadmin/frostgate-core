#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/artifacts}"
OUT_FILE="${OUT_FILE:-$OUT_DIR/CONTEXT_SNAPSHOT.md}"

mkdir -p "$OUT_DIR"

cd "$ROOT"

ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
branch="$(git branch --show-current 2>/dev/null || echo "unknown")"
commit="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"

{
  echo "# FrostGate Core Context Snapshot"
  echo
  echo "- Timestamp (UTC): \`$ts\`"
  echo "- Branch: \`$branch\`"
  echo "- Commit: \`$commit\`"
  echo "- Repo: \`$ROOT\`"
  echo

  echo "## Quick Run Commands"
  echo '```bash'
  echo "cd $ROOT"
  echo "source .venv/bin/activate"
  echo "make test"
  echo "make e2e-local"
  echo '```'
  echo

  echo "## Environment Expectations (common)"
  echo '```bash'
  echo "FG_ENV=${FG_ENV:-dev}"
  echo "FG_AUTH_ENABLED=${FG_AUTH_ENABLED:-1}"
  echo "FG_API_KEY=${FG_API_KEY:-demo_key_change_me}"
  echo "FG_SQLITE_PATH=${FG_SQLITE_PATH:-$ROOT/state/frostgate.db}"
  echo '```'
  echo

  echo "## Git Status"
  echo '```'
  git status -sb || true
  echo '```'
  echo

  echo "## Git Diff (working tree)"
  echo '```diff'
  git diff || true
  echo '```'
  echo

  echo "## Repo Tree (3 levels)"
  echo '```'
  if command -v tree >/dev/null 2>&1; then
    tree -a -L 3 -I "state|artifacts|.venv|__pycache__|.pytest_cache|node_modules|.git" .
  else
    # fallback: find
    find . -maxdepth 3 \
      -not -path "./.git/*" \
      -not -path "./.venv/*" \
      -not -path "./artifacts/*" \
      -not -path "./state/*" \
      -not -path "*/__pycache__/*" \
      -not -path "./.pytest_cache/*" \
      -print | sed 's|^\./||'
  fi
  echo '```'
  echo

  echo "## Python Deps (pip freeze)"
  echo '```'
  .venv/bin/python -m pip freeze 2>/dev/null || true
  echo '```'
  echo

  echo "## Pytest Config + Collected Tests"
  echo "### pytest.ini"
  echo '```'
  sed -n '1,200p' pytest.ini 2>/dev/null || true
  echo '```'
  echo
  echo "### test list"
  echo '```'
  .venv/bin/python -m pytest --collect-only -q 2>/dev/null || true
  echo '```'
  echo

  echo "## FastAPI Routes (from build_app(False))"
  echo '```'
  .venv/bin/python - <<'PY' 2>/dev/null || true
from api.main import build_app
app = build_app(False)
paths = sorted({getattr(r, "path", None) for r in app.routes if getattr(r, "path", None)})
print("\n".join([p for p in paths if p]))
PY
  echo '```'
  echo

  echo "## Key Files (trimmed)"
  for f in \
    api/main.py \
    api/defend.py \
    api/stats.py \
    api/feed.py \
    api/db.py \
    api/db_models.py \
    Makefile \
    .github/workflows/ci.yml \
    scripts/snapshot_context.sh \
  ; do
    if [ -f "$f" ]; then
      echo "### $f"
      echo '```'
      # Keep this readable: first 250 lines + last 80 lines
      sed -n '1,250p' "$f" || true
      echo
      echo "----- snip -----"
      echo
      tail -n 80 "$f" || true
      echo '```'
      echo
    fi
  done

  echo "## Open Issues / TODO Grep"
  echo '```'
  rg -n "TODO|FIXME|HACK|XXX" api tests scripts 2>/dev/null || true
  echo '```'
  echo

  echo "## Contract surfaces (grep)"
  echo '```'
  rg -n "build_app\(|/health|/status|auth_enabled|FG_AUTH_ENABLED|FG_API_KEY" api tests 2>/dev/null || true
  echo '```'
  echo

} > "$OUT_FILE"

echo "✅ Wrote snapshot: $OUT_FILE"
