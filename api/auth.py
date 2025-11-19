from fastapi import Header, HTTPException, status, Depends
from .config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()

    # If auth is disabled, always allow
    if not settings.auth_enabled:
        return

    if x_api_key is None or x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
