# engine/settings.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FROSTGATE_",
        case_sensitive=False,
    )

    # Env / mode
    env: str = "dev"
    enforcement_mode: str = "observe"
    log_level: str = "INFO"

    # Auth
    auth_enabled: bool = False
    api_key: str | None = None


settings = Settings()
