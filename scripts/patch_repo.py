#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(".").resolve()
EXCLUDE = {".venv", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}

def iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if any(part in EXCLUDE for part in p.parts):
            continue
        yield p

def sub_file(path: Path, pattern: str, repl: str, flags=re.MULTILINE) -> int:
    s = path.read_text(encoding="utf-8")
    ns, n = re.subn(pattern, repl, s, flags=flags)
    if n:
        path.write_text(ns, encoding="utf-8")
        print(f"[patch] {path}: {n} change(s)")
    return n

def delete_main_save():
    p = ROOT / "api" / "main.py.save"
    if p.exists():
        p.unlink()
        print("[patch] deleted api/main.py.save (should not be in repo)")

def patch_rate_limit_imports():
    # Normalize everything to api.ratelimit
    for p in iter_py_files(ROOT):
        sub_file(p, r"^from api\.rate_limit import rate_limit_guard\s*$",
                   "from api.ratelimit import rate_limit_guard")
        # Also fix common wrong module name in try/except blocks
        sub_file(p, r"from api\.rate_limit import rate_limit_guard",
                   "from api.ratelimit import rate_limit_guard")

def patch_ingest_rules_triggered_column():
    ingest = ROOT / "api" / "ingest.py"
    if not ingest.exists():
        return
    # Fix DecisionRecord(...) assignment to use rules_triggered_json not rules_triggered
    s = ingest.read_text(encoding="utf-8")

    # If ingest already uses rules_triggered_json, nothing to do
    if "rules_triggered_json=" in s:
        return

    # Replace the exact field assignment if present
    ns, n = re.subn(
        r"\brules_triggered\s*=\s*_safe_json\((rules)\)\s*,\s*#.*$",
        r"rules_triggered_json=_safe_json(\1),",
        s,
        flags=re.MULTILINE,
    )
    if n == 0:
        # broader: any rules_triggered=_safe_json(...)
        ns, n = re.subn(
            r"\brules_triggered\s*=\s*_safe_json\(([^)]+)\)\s*,?",
            r"rules_triggered_json=_safe_json(\1),",
            s,
            flags=re.MULTILINE,
        )

    if n:
        ingest.write_text(ns, encoding="utf-8")
        print(f"[patch] api/ingest.py: fixed DecisionRecord column name ({n} edits)")

def patch_duplicate_telemetryinput_in_main():
    """
    api/main.py defines TelemetryInput but api/schemas.py also defines it.
    Canonical = api.schemas.TelemetryInput.

    This patch:
      - removes the TelemetryInput class block from api/main.py (if present)
      - ensures api.main imports TelemetryInput from api.schemas
    """
    main = ROOT / "api" / "main.py"
    if not main.exists():
        return
    s = main.read_text(encoding="utf-8")

    # Ensure import exists
    if "from api.schemas import TelemetryInput" not in s:
        # add near other api.* imports
        s = re.sub(
            r"(from api\.[^\n]+\n)+",
            lambda m: m.group(0) + "from api.schemas import TelemetryInput\n",
            s,
            count=1,
            flags=re.MULTILINE,
        )

    # Remove class TelemetryInput(BaseModel): ... block if it exists
    # (crude but works: remove from 'class TelemetryInput' until next 'class ' at column 0)
    s2, n = re.subn(
        r"^class\s+TelemetryInput\s*\(BaseModel\)\s*:\n(?:^[ \t].*\n)+(?=^class\s|\Z)",
        "",
        s,
        flags=re.MULTILINE,
    )
    if n:
        print("[patch] api/main.py: removed duplicate TelemetryInput class (use api.schemas.TelemetryInput)")
        s = s2

    main.write_text(s, encoding="utf-8")

def main():
    print("== patch_repo starting ==")
    delete_main_save()
    patch_rate_limit_imports()
    patch_ingest_rules_triggered_column()
    patch_duplicate_telemetryinput_in_main()
    print("== patch_repo done ==")

if __name__ == "__main__":
    main()
