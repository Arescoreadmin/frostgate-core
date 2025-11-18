import os

from pydantic import BaseModel


class Settings(BaseModel):
    env: str = os.getenv("FG_ENV", "dev")
    log_indexer_url: str = os.getenv("LOG_INDEXER_URL", "http://loki:3100")
    pq_fallback_header: str = os.getenv("PQ_FALLBACK_HEADER", "x-pq-fallback")

    # enforce | observe
    enforcement_mode: str = os.getenv("FG_ENFORCEMENT_MODE", "enforce").lower()

    ai_adversarial_threshold: float = float(os.getenv("AI_ADV_SCORE_THRESHOLD", "0.8"))
    clock_drift_warn_ms: int = int(os.getenv("CLOCK_DRIFT_WARN_MS", "1000"))


settings = Settings()
