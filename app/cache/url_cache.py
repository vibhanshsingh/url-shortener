"""
The cache-aside pattern from Milestone 1, implemented for real.

Cache value shape: a small JSON object, not a bare string. We need
`expires_at` alongside `long_url` so a cache HIT can still correctly
answer "has this link expired?" without touching Postgres — if we only
cached the raw long_url, expiry checks would silently stop working the
moment a code became cache-warm.

We deliberately do NOT cache negative results (short codes that don't
exist) in this milestone — that's a legitimate technique ("negative
caching," protecting the DB from repeated lookups of a code that will
never resolve) but it adds its own complexity (a separate, usually
shorter TTL, and a real risk of caching a false negative for a code
that gets created moments later). Worth knowing the technique exists;
not in scope here.

MILESTONE 13 — graceful degradation: every method now catches Redis
connection failures and treats them as "cache unavailable" rather than
letting the error crash the caller. get() returns None (same as a
normal miss) instead of raising; set()/invalidate() log and move on.
The circuit breaker means that once Redis has failed a few times in a
row, we stop even attempting the call for a cooldown window — failing
fast instead of paying a multi-second timeout on every single request
while Redis is down.
"""

import json
import logging
from datetime import datetime

import redis.asyncio as redis

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "url:"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class URLCache:
    def __init__(self, client: redis.Redis):
        self._client = client
        # One breaker shared across get/set/invalidate for this cache
        # instance — a Redis outage affects all three equally, so they
        # should trip and recover together, not independently.
        self._breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=15.0)

    @staticmethod
    def _key(short_code: str) -> str:
        # Namespaced key ("url:aB2KdP" not just "aB2KdP") so this
        # Redis instance can safely hold other kinds of cached data
        # later (rate-limit counters, analytics cache, etc.) without
        # key collisions.
        return f"{CACHE_KEY_PREFIX}{short_code}"

    async def get(self, short_code: str) -> dict | None:
        """Returns {'long_url': str, 'expires_at': str | None} on a
        cache hit, or None on a miss OR on any Redis failure — from
        the caller's point of view, "Redis is down" and "this key
        doesn't exist" look identical: both mean "go check Postgres."
        That's the essence of graceful degradation here: the failure
        mode of the cache degrades to its own cache-miss behavior."""
        try:
            raw = await self._breaker.call(lambda: self._client.get(self._key(short_code)))
        except (CircuitOpenError, Exception) as exc:
            if not isinstance(exc, CircuitOpenError):
                logger.warning("Redis GET failed, degrading to cache-miss: %s", exc)
            return None

        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def set(
        self, short_code: str, long_url: str, expires_at: datetime | None, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        value = json.dumps(
            {"long_url": long_url, "expires_at": expires_at.isoformat() if expires_at else None}
        )
        try:
            await self._breaker.call(
                lambda: self._client.set(self._key(short_code), value, ex=ttl_seconds)
            )
        except CircuitOpenError:
            pass  # circuit already open — don't even log every single skipped attempt
        except Exception as exc:
            # Failing to WARM the cache is never fatal — the next read
            # will just be a cache miss and fall back to Postgres,
            # exactly like it always did before Milestone 7 existed.
            logger.warning("Redis SET failed, continuing without caching: %s", exc)

    async def invalidate(self, short_code: str) -> None:
        """
        Explicit invalidation — call this the moment a URL is
        soft-deleted or its long_url changes, rather than waiting for
        the TTL to expire. Not called anywhere yet (there's no delete
        endpoint in this milestone), but the seam is here for when
        Milestone 15 or a future milestone adds one — deleting a URL
        without also calling this would leave stale, redirect-worthy
        data serving from cache for up to DEFAULT_TTL_SECONDS.
        """
        try:
            await self._breaker.call(lambda: self._client.delete(self._key(short_code)))
        except CircuitOpenError:
            pass
        except Exception as exc:
            logger.warning("Redis DELETE failed during invalidate: %s", exc)
