from pathlib import Path
import re

p = Path("Makefile")
s = p.read_text()

# Replace any existing kill-uvicorn section (whether correct or broken)
# We match from ".PHONY: kill-uvicorn" through the next blank line or next .PHONY/target header
pattern = re.compile(
    r"(?ms)^\.PHONY:\s*kill-uvicorn\s*\n"
    r"(?:^[^\S\r\n].*\n)*"             # indented junk lines (broken recipes)
    r"(?:^kill-uvicorn\s*:.*\n(?:^\t.*\n)*)?"  # optional proper target+recipe
)

replacement = (
    ".PHONY: kill-uvicorn\n"
    "kill-uvicorn:\n"
    "\t-@echo \"Killing stray uvicorn processes...\"\n"
    "\t-sudo pkill -f \"uvicorn app.main:app\" || true\n"
    "\t-sudo pkill -f \"python -m uvicorn app.main:app\" || true\n"
    "\t-pkill -f \"uvicorn api.main:app\" || true\n"
    "\t-pkill -f \"python -m uvicorn api.main:app\" || true\n"
    "\t-pkill -f \".venv/bin/uvicorn api.main:app\" || true\n"
    "\t-@lsof -iTCP:8000 -sTCP:LISTEN -nP || true\n"
    "\t-@lsof -iTCP:8080 -sTCP:LISTEN -nP || true\n"
    "\n"
)

if not re.search(r"(?m)^\.PHONY:\s*kill-uvicorn\s*$", s):
    # If the block doesn't exist at all, append near the end
    s = s.rstrip() + "\n\n" + replacement
else:
    s, n = pattern.subn(replacement, s, count=1)
    if n == 0:
        # Fallback: brute force remove any target header and reinsert
        s = re.sub(r"(?ms)^kill-uvicorn\s*:.*?(?=^\S|\Z)", "", s)
        s = re.sub(r"(?m)^\.PHONY:\s*kill-uvicorn\s*$", "", s)
        s = s.rstrip() + "\n\n" + replacement

p.write_text(s)
print("✅ kill-uvicorn block normalized")
