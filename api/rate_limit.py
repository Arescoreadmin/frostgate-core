from __future__ import annotations

from fastapi import Request

def rate_limit_guard():
    # Proper FastAPI dependency: only injectable params (Request), no *args/**kwargs
    async def _dep(request: Request):
        # TODO: implement real limiter (redis token bucket, etc.)
        return None
    return _dep
