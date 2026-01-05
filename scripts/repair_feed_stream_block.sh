#!/usr/bin/env bash
set -u  # controlled failures, no terminal nuking
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || { echo "❌ can't cd to repo root"; exit 2; }

python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

# --- 1) Ensure required imports exist (minimally invasive) ---

# Ensure we have Request/Response available from fastapi import line(s)
# If fastapi import is split across multiple lines, just insert dedicated imports.
if "from fastapi import Response" not in s:
    s = re.sub(r'^(from fastapi import .*)$',
               lambda m: (m.group(1) if "Response" in m.group(1) else m.group(1) + ", Response"),
               s, count=1, flags=re.M)
    if "from fastapi import Response" not in s:
        # fallback: add near other fastapi imports
        s = s.replace("from fastapi import APIRouter, Depends, Query\n",
                      "from fastapi import APIRouter, Depends, Query, Response\n")

if "Request" not in s:
    # likely already imported somewhere, but if not, add it
    s = re.sub(r'^(from fastapi import .*)$',
               lambda m: (m.group(1) if "Request" in m.group(1) else m.group(1) + ", Request"),
               s, count=1, flags=re.M)
    if "Request" not in s:
        s = s.replace("from fastapi import APIRouter, Depends, Query, Response\n",
                      "from fastapi import APIRouter, Depends, Query, Response, Request\n")

# Ensure StreamingResponse import
if "StreamingResponse" not in s:
    if "from starlette.responses import" in s:
        s = re.sub(r'^(from starlette\.responses import .*)$',
                   lambda m: (m.group(1) if "StreamingResponse" in m.group(1) else m.group(1) + ", StreamingResponse"),
                   s, count=1, flags=re.M)
    else:
        # add near top after other imports
        s = s.replace("from sqlalchemy.orm import Session\n",
                      "from sqlalchemy.orm import Session\n\nfrom starlette.responses import StreamingResponse\n")

# Ensure asyncio + json imports
if re.search(r'^\s*import asyncio\s*$', s, flags=re.M) is None:
    s = s.replace("from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport asyncio\n")
if re.search(r'^\s*import json\s*$', s, flags=re.M) is None:
    s = s.replace("import asyncio\n", "import asyncio\nimport json\n")

# --- 2) Replace the entire stream region with a known-good block ---

# Find the GET /stream decorator
m_get = re.search(r'^\s*@router\.get\(\s*"/stream"[^\)]*\)\s*$', s, flags=re.M)
if not m_get:
    print("❌ Could not find @router.get(\"/stream\") in api/feed.py")
    raise SystemExit(3)

# Find start of region (prefer @router.head("/stream") if it exists above GET)
m_head = None
for mh in re.finditer(r'^\s*@router\.head\(\s*"/stream"\s*\)\s*$', s, flags=re.M):
    if mh.start() < m_get.start():
        m_head = mh

start = m_head.start() if m_head else m_get.start()

# Find end of feed_stream function by scanning forward from GET decorator
tail = s[m_get.start():]
m_def = re.search(r'^\s*async\s+def\s+feed_stream\s*\(', tail, flags=re.M)
if not m_def:
    print("❌ Found GET /stream decorator but not async def feed_stream(")
    raise SystemExit(4)

# Determine function block end: next decorator at column 0 or end-of-file
after_def = tail[m_def.start():]
m_next = re.search(r'^\s*@router\.', after_def, flags=re.M)
if m_next:
    # If the next decorator is the GET itself (edge case), skip first and find second
    # Safer: search after the first line of GET decorator + def block
    pass

# We’ll locate end by searching from the *def* start for the next top-level decorator OR end-of-file.
def_abs_start = m_get.start() + m_def.start()
rest = s[def_abs_start:]
m_end = re.search(r'^\s*@router\.', rest, flags=re.M)
if m_end:
    # If it hits the decorator at the same position (unlikely), ignore first match by slicing
    # More robust: find the first decorator AFTER the def line ends.
    lines = rest.splitlines(True)
    # find index after the feed_stream def line
    idx = 0
    for j, ln in enumerate(lines):
        if re.match(r'^\s*async\s+def\s+feed_stream\s*\(', ln):
            idx = j + 1
            break
    rest2 = "".join(lines[idx:])
    m_end2 = re.search(r'^\s*@router\.', rest2, flags=re.M)
    if m_end2:
        end = def_abs_start + (len("".join(lines[:idx])) + m_end2.start())
    else:
        end = len(s)
else:
    end = len(s)

stream_block = r'''
@router.head("/stream")
def feed_stream_head() -> Response:
    # Headers-only probe for smoke tests / health checks
    return Response(content=b"", media_type="text/event-stream")


@router.get("/stream")
async def feed_stream(
    request: Request,
    db: Session = Depends(get_db),
    since_id: int | None = None,
    limit: int = 50,
    interval: float = 1.0,
):
    """
    Server-Sent Events stream for the live feed.
    Emits:
      event: items
      data: {"items":[...], "next_since_id": N}
    """
    async def event_gen():
        nonlocal since_id
        # Suggest client retry quickly
        yield "retry: 1000\n\n"

        while True:
            # disconnect detection (best effort)
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                pass

            fn = globals().get("feed_live")
            if fn is None:
                yield 'event: error\ndata: {"detail":"feed_live not found"}\n\n'
                break

            resp = fn(db=db, since_id=since_id, limit=limit)
            if hasattr(resp, "__await__"):
                resp = await resp

            data = resp.model_dump() if hasattr(resp, "model_dump") else (resp.dict() if hasattr(resp, "dict") else resp)

            try:
                since_id = data.get("next_since_id") or since_id
            except Exception:
                pass

            payload = json.dumps(data, separators=(",", ":"), default=str)
            yield "event: items\n"
            yield "data: " + payload + "\n\n"

            try:
                await asyncio.sleep(max(0.2, float(interval)))
            except Exception:
                await asyncio.sleep(1.0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
'''.lstrip("\n")

s2 = s[:start] + stream_block + s[end:]
p.write_text(s2)
print("✅ Rewrote /feed/stream block (HEAD+GET) to a known-good implementation")
PY

python -m py_compile api/feed.py || { echo "❌ py_compile failed"; exit 1; }
echo "✅ api/feed.py compiles"
