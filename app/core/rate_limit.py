"""Rate limiting middleware backed by Redis (falls back to in-memory)."""

import time
import logging
from typing import Callable
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings

logger = logging.getLogger(__name__)

_redis_available = False
_redis_client = None


async def _get_redis():
    global _redis_available, _redis_client
    if _redis_available and _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await _redis_client.ping()
        _redis_available = True
        logger.info("Rate limiter: using Redis backend")
        return _redis_client
    except Exception:
        _redis_available = False
        logger.warning("Rate limiter: Redis unavailable, falling back to in-memory")
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiter — Redis-backed with in-memory fallback."""

    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._in_memory: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        if path.startswith("/api/v1/health") or path.startswith("/api/v1/webhooks") or path == "/metrics":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - self.window_seconds

        redis = await _get_redis()
        key = f"ratelimit:{client_ip}"

        if redis is not None:
            try:
                pipe = redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                pipe.zadd(key, {str(now): now})
                pipe.expire(key, self.window_seconds)
                _, count, _, _ = await pipe.execute()
                if count >= self.max_requests:
                    raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
                return await call_next(request)
            except HTTPException:
                raise
            except Exception:
                pass  # Fall through to in-memory

        # In-memory fallback
        if key in self._in_memory:
            self._in_memory[key] = [t for t in self._in_memory[key] if t > window_start]
        else:
            self._in_memory[key] = []

        if len(self._in_memory[key]) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")

        self._in_memory[key].append(now)
        return await call_next(request)
