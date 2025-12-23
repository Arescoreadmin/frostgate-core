from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from api.auth_scopes import require_scopes

log = logging.getLogger("frostgate.feed")

router = APIRouter()

@router.get("/feed/live", dependencies=[Depends(require_scopes("feed:read"))])
def feed_live(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    log.debug("feed.live limit=%s", limit)
    return {"items": [], "limit": limit}
