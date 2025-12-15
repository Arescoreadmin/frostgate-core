from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from fastapi import Depends, HTTPException, Request

from api.auth import verify_api_key

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


# -----------------------------
# Config
# -----------------------------

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _env_csv(name: str, default: str = "") -> set[str]:
    v = os.getenv(name, default).strip()
    if not v:
        return set()
    return {s.strip() for s in v.split(",") if s.strip()}


@dataclass(frozen=True)
class RLConfig:
    enabled: bool
    backend: str             # "redis" (recommended) | "memory" (not provided here)
    scope: str               # "tenant" | "source" | "ip"
    paths: set[str]
    bypass_keys: set[str]

    # Token bucket
    rate_per_sec: float      # refill rate (tokens/sec)
    burst: int               # extra burst capacity

    # Redis
    redis_url: str
    prefix: str              # key namespace

    # Failure behavior
    fail_open: bool          # if redis fails, allow requests


def load_config() -> RLConfig:
    enabled = _env_bool("FG_RL_ENABLED", True)
    backend = os.getenv("FG_RL_BACKEND", "redis").strip().lower()
    scope = os.getenv("FG_RL_SCOPE", "tenant").strip().lower()
    paths = _env_csv("FG_RL_PATHS", "/defend")
    bypass_keys = _env_csv("FG_RL_BYPASS_KEYS", "")

    rate = _env_float("FG_RL_RATE_PER_SEC", 2.0)
    burst = _env_int("FG_RL_BURST", 60)

    redis_url = os.getenv("FG_REDIS_URL", "redis://localhost:6379/0").strip()
    prefix = os.getenv("FG_RL_PREFIX", "fg:rl").strip()

    fail_open = _env_bool("FG_RL_FAIL_OPEN", True)

    if backend not in ("redis",):
        backend = "redis"
    if scope not in ("tenant", "source", "ip"):
        scope = "tenant"

    if rate <= 0:
        rate = 1.0
    if burst < 0:
        burst = 0

    return RLConfig(
        enabled=enabled,
        backend=backend,
        scope=scope,
        paths=paths,
        bypass_keys=bypass_keys,
        rate_per_sec=rate,
        burst=burst,
        redis_url=redis_url,
        prefix=prefix,
        fail_open=fail_open,
    )


# -----------------------------
# Keying
# -----------------------------

def _api_key_from_request(request: Request) -> str:
    return (request.headers.get("x-api-key") or "").strip()


def _key_from_request(request: Request, cfg: RLConfig) -> str:
    body = getattr(request.state, "telemetry_body", None)
    tenant = None
    source = None
    if isinstance(body, dict):
        tenant = body.get("tenant_id")
        source = body.get("source")

    if cfg.scope == "tenant" and tenant:
        return f"tenant:{tenant}"
    if cfg.scope == "source" and source:
        return f"source:{source}"

    xfwd = request.headers.get("x-forwarded-for")
    if xfwd:
        ip = xfwd.split(",")[0].strip()
        return f"ip:{ip}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


# -----------------------------
# Redis token bucket (atomic)
# -----------------------------
# State per key:
#  - tokens (float)
#  - last_ts (float seconds)
#
# Capacity = burst + rate_per_sec (optional) ??? No, keep it simple:
# capacity = burst + rate_per_sec * 1 second? not meaningful.
# Standard token bucket: capacity = burst (or burst + base). Here:
# capacity = burst + (rate_per_sec) so you can always do at least ~1s worth after idle.
#
# We'll set capacity = burst + max(1, rate_per_sec) to avoid tiny caps.
#
# Returns:
#  allowed (0/1), limit, remaining, reset_seconds
#
_LUA_TOKEN_BUCKET = r"""
-- KEYS[1] = bucket key
-- ARGV[1] = now (float seconds)
-- ARGV[2] = rate_per_sec (float)
-- ARGV[3] = capacity (float)
-- ARGV[4] = cost (float) (usually 1)

local key = KEYS[1]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

-- refill
local delta = now - ts
if delta < 0 then
  delta = 0
end

tokens = math.min(capacity, tokens + (delta * rate))
ts = now

local allowed = 0
local remaining = tokens

if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
  remaining = tokens
else
  allowed = 0
  remaining = 0
end

-- compute reset: time until 1 token available
local reset = 0
if allowed == 0 then
  local needed = cost - tokens
  reset = math.ceil(needed / rate)
  if reset < 1 then reset = 1 end
end

-- persist
redis.call("HMSET", key, "tokens", tokens, "ts", ts)

-- set expiry to avoid infinite key growth:
-- expire after enough time to refill to capacity plus some slack
local ttl = math.ceil((capacity / rate) * 2)
if ttl < 60 then ttl = 60 end
redis.call("EXPIRE", key, ttl)

-- "limit" here is effectively capacity per burst, not a window cap.
-- We'll present it as a per-second rate + burst in headers in a consistent way.
return {allowed, capacity, math.floor(remaining), reset}
"""

_redis_client = None
_redis_script = None


def _get_redis(cfg: RLConfig):
    global _redis_client, _redis_script
    if _redis_client is not None:
        return _redis_client, _redis_script

    if redis is None:
        raise RuntimeError("redis package not installed")

    _redis_client = redis.Redis.from_url(cfg.redis_url, decode_responses=True)
    _redis_script = _redis_client.register_script(_LUA_TOKEN_BUCKET)
    return _redis_client, _redis_script


def _capacity(cfg: RLConfig) -> float:
    # Give at least 1 token base, plus burst.
    base = max(1.0, cfg.rate_per_sec)
    return float(cfg.burst) + base


def _allow_redis(key: str, cfg: RLConfig) -> Tuple[bool, int, int, int]:
    r, script = _get_redis(cfg)

    now = time.time()
    cap = _capacity(cfg)
    cost = 1.0

    redis_key = f"{cfg.prefix}:{key}:tb"
    allowed, limit, remaining, reset = script(
        keys=[redis_key],
        args=[f"{now}", f"{cfg.rate_per_sec}", f"{cap}", f"{cost}"],
    )

    ok = bool(int(allowed))
    # limit as int for headers
    return ok, int(float(limit)), int(float(remaining)), int(float(reset))


# -----------------------------
# FastAPI dependency
# -----------------------------

async def rate_limit_guard(
    request: Request,
    _: Any = Depends(verify_api_key),
) -> None:
    cfg = load_config()
    if not cfg.enabled:
        return

    if request.url.path not in cfg.paths:
        return

    api_key = _api_key_from_request(request)
    if api_key and api_key in cfg.bypass_keys:
        return

    key = _key_from_request(request, cfg)

    try:
        ok, limit, remaining, reset = _allow_redis(key, cfg)
    except Exception:
        if cfg.fail_open:
            return
        raise HTTPException(status_code=503, detail="Rate limiter unavailable")

    headers = {
        "Retry-After": str(reset if not ok else 0),
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset if not ok else 0),
        # helpful for debugging clients:
        "X-RateLimit-Policy": f"tb;rate={cfg.rate_per_sec}/s;burst={cfg.burst};scope={cfg.scope}",
    }

    if not ok:
        raise HTTPException(status_code=429, detail="Rate limit exceeded", headers=headers)
