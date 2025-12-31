#!/usr/bin/env bash
set -euo pipefail

echo "[*] git:"
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --porcelain || true
echo

echo "[*] tree (3 levels):"
python - <<'PY'
import os
from pathlib import Path

root = Path(".")
max_depth = 3

def walk(p: Path, depth: int):
    if depth > max_depth: return
    for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        rel = child.relative_to(root)
        indent = "  " * depth
        if child.is_dir():
            print(f"{indent}{rel}/")
            if child.name in {".git", ".venv", "__pycache__", ".pytest_cache"}:
                continue
            walk(child, depth + 1)
        else:
            print(f"{indent}{rel}")
walk(root, 0)
PY
echo

echo "[*] key files:"
for f in api/main.py api/defend.py api/db_models.py api/db.py api/auth_scopes.py api/decisions.py api/feed.py Makefile; do
  [ -f "$f" ] && echo "---- $f ----" && sed -n '1,220p' "$f" && echo
done

echo "[*] compile:"
python -m py_compile api/defend.py api/main.py

echo "[*] tests:"
pytest -q -m "not integration"
