"""
Plain-language version of what this file does:

For every request that comes in, we:
  1. Figure out the caller's IP address and the current minute.
  2. Build one Redis key for "this IP, this minute."
  3. Add 1 to that key.
  4. If the new count is over the limit, say no (429). Otherwise, let
     the request through as normal.
  5. Redis automatically forgets the key after 60 seconds, so we never
     have to clean up old counters ourselves.

Middleware (not a per-route dependency) is the right place for this,
because we want EVERY request checked the same way, without having to
remember to add "Depends(rate_limit)" to every single route by hand —
one place, applied automatically, nothing to forget.
"""

import logging
import time

import redis.asyncio as redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.metrics import rate_limit_exceeded_total

# Health checks are hit constantly by Docker/an orchestrator, and
# /metrics is hit constantly by Prometheus — neither should ever be
# rate-limited, or routine infrastructure polling could get itself
# blocked and misreported as "unhealthy" or "no data."
EXEMPT_PATHS = {"/health/live", "/health/ready", "/metrics"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: redis.Redis):
        super().__init__(app)
        self._redis = redis_client

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # "Which minute are we in" as a plain number — e.g. the number
        # of whole minutes since 1970. Two requests in the same minute
        # get the exact same number here, which is what makes them
        # share one counter.
        current_minute = int(time.time() // 60)
        key = f"ratelimit:{client_ip}:{current_minute}"

        # INCR both creates the key at 1 (if it's the first request
        # this minute) and adds 1 to it (every time after) — one
        # command does both jobs.
        try:
            count = await self._redis.incr(key)
            if count == 1:
                # Only set the auto-expire on the very first request of
                # this window — setting it again on every request would
                # keep pushing the expiry forward and the "bucket" would
                # never actually reset.
                await self._redis.expire(key, 60)
        except Exception as exc:
            # Redis is a best-effort dependency for rate limiting.
            # If it is unavailable, we should not prevent the request
            # from reaching the application or from being served from
            # Postgres.
            logging.warning(
                "Rate limiting unavailable, degrading to no rate limit: %s",
                exc,
            )
            return await call_next(request)

        if count > settings.rate_limit_requests_per_minute:
            rate_limit_exceeded_total.inc()
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: max "
                        f"{settings.rate_limit_requests_per_minute} requests per minute."
                    )
                },
                # Tells a well-behaved client exactly how long to wait
                # before trying again, instead of making it guess.
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
