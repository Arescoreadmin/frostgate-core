#!/usr/bin/env python3
import os, sys, tempfile
from pathlib import Path

def main():
    if len(sys.argv) != 2:
        print("usage: write_file.py <path>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)

    data = sys.stdin.read()
    fd, tmppath = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmppath, path)
    finally:
        try:
            if os.path.exists(tmppath):
                os.unlink(tmppath)
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
