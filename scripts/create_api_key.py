from __future__ import annotations

import secrets
import sys
from pathlib import Path

DEFAULT_KEYS_FILE = Path("secrets/fg_api_keys.txt")

def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python scripts/create_api_key.py <PREFIX> <scopes_csv> [name] [keys_file]")
        print('example: python scripts/create_api_key.py ADMIN "decisions:read,defend:write,ingest:write" "Admin Key"')
        raise SystemExit(2)

    prefix_in = sys.argv[1].strip().upper()
    scopes_csv = sys.argv[2].strip()
    name = sys.argv[3].strip() if len(sys.argv) > 3 else ""
    keys_file = Path(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_KEYS_FILE

    prefix = prefix_in.replace("_", "") + "_"
    raw_key = prefix + secrets.token_urlsafe(32)

    entry = f"{raw_key}|{scopes_csv}"

    keys_file.parent.mkdir(parents=True, exist_ok=True)
    if keys_file.exists():
        content = keys_file.read_text(encoding="utf-8")
        content = content.strip()  # normalize; remove trailing newlines/spaces
        if content:
            content = content + ";" + entry + "\n"
        else:
            content = entry + "\n"
    else:
        content = entry + "\n"

    # atomic-ish write
    tmp = keys_file.with_suffix(keys_file.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(keys_file)

    print("\nNEW_API_KEY=" + raw_key)
    print("prefix=" + prefix)
    print("scopes=" + scopes_csv)
    if name:
        print("name=" + name)
    print("file=" + str(keys_file.resolve()))

if __name__ == "__main__":
    main()
