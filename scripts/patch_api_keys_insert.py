from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(".")
TARGET_GLOBS = ["api/**/*.py", "engine/**/*.py", "agent/**/*.py"]

needle_sql = r"INSERT\s+INTO\s+api_keys\s*\(\s*key\s*,\s*scopes\s*,\s*is_active\s*\)"
replacement_sql = "INSERT INTO api_keys (prefix, key_hash, scopes_csv, enabled)"

# Try to also patch VALUES tuple shapes if present.
values_pattern = re.compile(
    r"VALUES\s*\(\s*\$1\s*,\s*\$2\s*,\s*true\s*\)",
    re.IGNORECASE | re.MULTILINE,
)

def glob_all():
    out = []
    for g in TARGET_GLOBS:
        out.extend(ROOT.glob(g))
    # de-dupe while preserving order
    seen = set()
    uniq = []
    for p in out:
        if p.is_file() and p not in seen and ".venv" not in p.parts and ".git" not in p.parts:
            uniq.append(p)
            seen.add(p)
    return uniq

def ensure_helper(src: str) -> str:
    """
    Ensure we have:
      - import hashlib
      - def _fg_key_prefix_and_hash(raw_key: str) -> tuple[str, str]
    and that it's placed somewhere sane (near other helpers / imports).
    """
    if "_fg_key_prefix_and_hash" in src:
        return src

    # Add hashlib import if missing.
    if re.search(r"^\s*import\s+hashlib\s*$", src, re.MULTILINE) is None:
        # Insert after other imports (best-effort).
        m = re.search(r"^(from\s+__future__.*\n+)?((?:import|from)\s+[^\n]+\n)+", src, re.MULTILINE)
        if m:
            block = m.group(0)
            if "hashlib" not in block:
                src = src.replace(block, block + "import hashlib\n")
        else:
            src = "import hashlib\n" + src

    helper = """
def _fg_key_prefix_and_hash(raw_key: str) -> tuple[str, str]:
    # prefix: used for quick identification (NOT a secret)
    # key_hash: sha256 hex digest (64 chars) to match api_keys.key_hash
    raw_key = (raw_key or "").strip()
    prefix = raw_key[:8] if len(raw_key) >= 8 else raw_key
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return prefix, key_hash

"""
    # Place helper after imports, before first def/class if possible
    m2 = re.search(r"^((?:from|import)\s+[^\n]+\n)+\n", src, re.MULTILINE)
    if m2:
        insert_at = m2.end()
        src = src[:insert_at] + helper + src[insert_at:]
    else:
        src = helper + src
    return src

def patch_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")

    if re.search(needle_sql, src, re.IGNORECASE) is None and "api_keys (key, scopes, is_active)" not in src:
        return False

    new = src

    # 1) Rewrite INSERT column list
    new = re.sub(needle_sql, replacement_sql, new, flags=re.IGNORECASE)

    # 2) Rewrite common VALUES placeholder tuple ($1,$2,true) -> ($1,$2,$3,$4)
    # But we don't know your DB driver. This catches the exact statement in your logs.
    new = values_pattern.sub("VALUES ($1, $2, $3, true)", new)

    # 3) Now we need to ensure code computes prefix/hash/scopes_csv.
    # We patch common patterns where raw key/scopes are used.
    # - If we see something like: (key, scopes) passed to execute, we rewrite it.

    # Patch common Python execute calls with tuple args:
    # execute(sql, (key, scopes))
    # -> prefix, key_hash = _fg_key_prefix_and_hash(key); scopes_csv = ",".join(scopes)
    #    execute(sql, (prefix, key_hash, scopes_csv))
    exec_tuple = re.compile(
        r"(\bexecute(?:many)?\s*\(\s*[^,]+,\s*)\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*,\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)\s*(\)\s*)",
        re.MULTILINE,
    )

    def repl_exec(m: re.Match) -> str:
        lead, k, scopes, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        injected = (
            f"prefix, key_hash = _fg_key_prefix_and_hash({k})\n"
            f"        scopes_csv = \",\".join({scopes}) if isinstance({scopes}, (list, tuple, set)) else str({scopes})\n"
        )
        return f"{injected}        {lead}(prefix, key_hash, scopes_csv){tail}"

    # Only inject if it looks like an insert into api_keys nearby
    if "INSERT INTO api_keys" in new:
        # crude indentation safety: only patch execute calls inside same file
        new = exec_tuple.sub(repl_exec, new)

        # Ensure helper exists
        new = ensure_helper(new)

    if new != src:
        path.write_text(new, encoding="utf-8")
        return True
    return False

def main():
    changed = []
    for p in glob_all():
        try:
            if patch_file(p):
                changed.append(str(p))
        except UnicodeDecodeError:
            continue

    if not changed:
        print("No matching api_keys insert pattern found. Run:")
        print("  grep -RIn --exclude-dir=.git --exclude-dir=.venv \"INSERT INTO api_keys\" .")
        raise SystemExit(2)

    print("Patched files:")
    for f in changed:
        print(" -", f)

if __name__ == "__main__":
    main()
