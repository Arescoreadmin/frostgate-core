#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


IGNORE = {
    # Make special targets
    ".PHONY",
    ".SUFFIXES",
    ".DEFAULT",
    ".PRECIOUS",
    ".SECONDARY",
    ".SECONDEXPANSION",
    ".INTERMEDIATE",
    ".DELETE_ON_ERROR",
    ".LOW_RESOLUTION_TIME",
    ".SILENT",
    ".EXPORT_ALL_VARIABLES",
    ".NOTPARALLEL",
    ".ONESHELL",
    ".POSIX",
}


def is_pattern_target(t: str) -> bool:
    return "%" in t


def parse_makefile_targets(text: str) -> List[Tuple[str, int]]:
    """
    Very simple parser:
      captures lines like: target: deps...
      ignores indented recipe lines
    """
    out: List[Tuple[str, int]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line or line[0].isspace():
            continue
        if line.startswith("#"):
            continue

        m = re.match(r"^([^\s:=]+(?:\s+[^\s:=]+)*)\s*:(?!=)", line)
        if not m:
            continue

        left = m.group(1)
        for t in left.split():
            t = t.strip()
            if not t or t in IGNORE:
                continue
            if is_pattern_target(t):
                continue
            out.append((t, i))
    return out


def main() -> int:
    makefile = Path("Makefile")
    if not makefile.exists():
        print("ERROR: Makefile not found", file=sys.stderr)
        return 2

    targets = parse_makefile_targets(makefile.read_text())

    seen: Dict[str, List[int]] = {}
    for t, ln in targets:
        seen.setdefault(t, []).append(ln)

    dups = {t: lns for t, lns in seen.items() if len(lns) > 1}

    if not dups:
        print("✅ Makefile target audit: no duplicate explicit targets.")
        return 0

    print("❌ Makefile target audit FAILED. Duplicate targets detected:\n", file=sys.stderr)
    for t, lns in sorted(dups.items()):
        print(f"  - {t}: lines {', '.join(map(str, lns))}", file=sys.stderr)

    print(
        "\nFix: rename one of the targets, or consolidate recipes. "
        "Do NOT rely on make warnings. They are how bugs hide.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
