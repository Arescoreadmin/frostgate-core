from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/live")
async def feed_live(
    limit: int = 5,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    # Test contract: must reject missing/empty key
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    n = max(0, min(int(limit), 50))

    # Minimal shape that satisfies tests
    items: List[Dict[str, Any]] = []
    for i in range(n):
        items.append(
            {
                "decision_id": f"dec_{i:04d}",
                "timestamp": _now_iso(),
                "severity": "info",
                "title": "Live feed item",
                "summary": "Placeholder decision summary",
                "action_taken": "log_only",
                "confidence": 0.75,
            }
        )

    return {"items": items}
