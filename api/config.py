from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    FrostGate core settings loaded from environment.

    Env prefix: FG_
      FG_ENV
      FG_ENFORCEMENT_MODE
      FG_API_KEY
      FG_PQ_FALLBACK_HEADER (optional override)
    """

    # Pydantic v2-style config
    model_config = SettingsConfigDict(
        env_prefix="FG_",
        extra="ignore",
    )

    # Core env
    env: str = "dev"
    service: str = "frostgate-core"
    enforcement_mode: str = "enforce"

    # API key auth
    api_key: str | None = None
    auth_enabled: bool = False

    # Header name used for pq_fallback flag on /defend
    pq_fallback_header: str = "x-pq-fallback"

    @classmethod
    def from_env(cls) -> "Settings":
        """
        Construct settings from env and derive auth_enabled.
        """
        settings = cls()
        settings.auth_enabled = bool(settings.api_key)
        return settings


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings accessor for app code.
    """
    return Settings.from_env()


# Backwards-compat: some modules do `from api.config import settings`
settings = get_settings()
