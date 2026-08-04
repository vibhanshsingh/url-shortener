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
"""

import json
from datetime import datetime

import redis.asyncio as redis

CACHE_KEY_PREFIX = "url:"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class URLCache:
    def __init__(self, client: redis.Redis):
        self._client = client

    @staticmethod
    def _key(short_code: str) -> str:
        # Namespaced key ("url:aB2KdP" not just "aB2KdP") so this
        # Redis instance can safely hold other kinds of cached data
        # later (rate-limit counters, analytics cache, etc.) without
        # key collisions.
        return f"{CACHE_KEY_PREFIX}{short_code}"

    async def get(self, short_code: str) -> dict | None:
        """Returns {'long_url': str, 'expires_at': str | None} on a
        cache hit, or None on a miss. Never raises on a malformed
        cached value — treats it as a miss instead, so a bad cache
        entry degrades to an extra DB read rather than a 500 error."""
        raw = await self._client.get(self._key(short_code))
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
        await self._client.set(self._key(short_code), value, ex=ttl_seconds)

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
        await self._client.delete(self._key(short_code))
