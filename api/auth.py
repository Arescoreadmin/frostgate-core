from fastapi import Header, HTTPException, status

from .config import settings


async def require_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")):
    """
    Simple global API key gate.

    If FG_API_KEY is not set, auth is effectively disabled.
    If set, x-api-key header must match.
    """
    if not settings.api_key:
        # Auth disabled; accept all.
        return

    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
