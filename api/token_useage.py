from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from fastapi import Request
from prometheus_client import Counter, Histogram

logger = logging.getLogger("frostgate.token_usage")

TOKEN_USAGE_REQUESTS = Counter(
    "frostgate_token_usage_requests_total",
    "Total API calls grouped by token fingerprint, endpoint, and status family",
    ["token_fingerprint", "endpoint", "status_family"],
)

TOKEN_USAGE_LATENCY_SECONDS = Histogram(
    "frostgate_token_usage_latency_seconds",
    "Request latency for API keys (token fingerprints) per endpoint",
    ["token_fingerprint", "endpoint"],
    buckets=[
        0.001,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
    ],
)


@dataclass
class TokenUsageStats:
    calls: int = 0
    successes: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    last_status_code: Optional[int] = None
    last_path: Optional[str] = None
    last_tenant_id: Optional[str] = None

    def record(
        self,
        *,
        path: str,
        status_code: int,
        latency_ms: float,
        tenant_id: Optional[str],
    ) -> None:
        self.calls += 1
        if status_code < 400:
            self.successes += 1
        else:
            self.errors += 1
        self.total_latency_ms += latency_ms
        self.last_status_code = status_code
        self.last_path = path
        if tenant_id:
            self.last_tenant_id = tenant_id

    def as_dict(self) -> Dict[str, object]:
        avg_latency_ms = (self.total_latency_ms / self.calls) if self.calls else 0.0
        return {
            **asdict(self),
            "avg_latency_ms": round(avg_latency_ms, 3),
        }


class TokenUsageTracker:
    """In-process tracker that fingerprints tokens and aggregates usage."""

    def __init__(self) -> None:
        self._stats: Dict[str, TokenUsageStats] = {}
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(token: str) -> str:
        # Stable, non-reversible identifier so logs/metrics never leak the token itself.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]

    def record(
        self,
        *,
        token: Optional[str],
        path: str,
        status_code: int,
        latency_ms: float,
        tenant_id: Optional[str],
    ) -> str:
        token_fp = self.fingerprint(token) if token else "missing"
        status_family = f"{status_code // 100}xx"

        with self._lock:
            stats = self._stats.setdefault(token_fp, TokenUsageStats())
            stats.record(
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                tenant_id=tenant_id,
            )

        TOKEN_USAGE_REQUESTS.labels(token_fp, path, status_family).inc()
        TOKEN_USAGE_LATENCY_SECONDS.labels(token_fp, path).observe(latency_ms / 1000.0)

        logger.info(
            "token_usage",
            extra={
                "token_fingerprint": token_fp,
                "endpoint": path,
                "status_code": status_code,
                "status_family": status_family,
                "latency_ms": latency_ms,
                "tenant_id": tenant_id,
            },
        )
        return token_fp

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        with self._lock:
            return {fp: stats.as_dict() for fp, stats in self._stats.items()}


token_usage_tracker = TokenUsageTracker()


async def token_usage_middleware(request: Request, call_next):
    """
    Observes every request to capture where API tokens are being used.

    - Fingerprints the presented token (never logs raw keys)
    - Emits Prometheus metrics per token/endpoint
    - Writes structured logs for auditability
    - Maintains an in-process snapshot for quick analysis endpoints
    """

    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - start) * 1000

    api_key = request.headers.get("x-api-key")
    tenant_id = request.headers.get("x-tenant-id")
    endpoint = request.url.path

    token_fp = token_usage_tracker.record(
        token=api_key,
        path=endpoint,
        status_code=response.status_code,
        latency_ms=latency_ms,
        tenant_id=tenant_id,
    )

    request.state.token_fingerprint = token_fp
    return response


def get_token_usage_snapshot() -> Dict[str, Dict[str, object]]:
    """Returns an aggregated view of token usage for quick efficiency analysis."""

    return token_usage_tracker.snapshot()
