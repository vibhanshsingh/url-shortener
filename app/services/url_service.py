"""
Service layer (recap from Milestone 1): business logic, no HTTP
concerns. This class doesn't know it's being called from a FastAPI
route — it could just as easily be called from a CLI script or a
background job, which is exactly why it's testable without spinning up
a test HTTP client.

Milestone 7 adds the cache-aside pattern to resolve_for_redirect: a
Redis hit skips Postgres entirely; a miss falls back to Postgres and
warms the cache for next time. This is the single most important
performance change in the whole redirect path.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.cache.url_cache import URLCache
from app.core.config import settings
from app.core.metrics import (
    cache_hits_total,
    cache_misses_total,
    redirects_total,
    urls_created_total,
)
from app.models.url import URL
from app.repository.url_repository import URLRepository
from app.services.encoding import encode


class SelfReferentialURLError(ValueError):
    """Raised when someone tries to shorten a URL that points back at
    this service itself — would create a confusing or infinite
    redirect chain."""


class URLNotFoundError(Exception):
    """No URL was ever created with this short code. Maps to 404."""


class URLGoneError(Exception):
    """A URL existed for this short code but is no longer usable
    (soft-deleted or past its expires_at). Maps to 410, not 404 —
    "used to exist, deliberately doesn't anymore" is a meaningfully
    different fact than "never existed" for an API consumer."""


@dataclass
class RedirectTarget:
    """
    Deliberately NOT the full URL model. resolve_for_redirect can
    satisfy its answer from either Redis (a cache hit) or Postgres (a
    miss) — the route layer only ever needs `long_url`, so this small,
    origin-agnostic shape is what both paths return. If we returned
    the full URL ORM model instead, the cache-hit path would either
    need to fake one up (fragile) or the route layer would need to
    know which origin it got data from (leaky abstraction).
    """

    long_url: str


class URLService:
    def __init__(self, repository: URLRepository, cache: URLCache):
        self._repository = repository
        self._cache = cache

    async def create_short_url(
        self, long_url: str, created_by_ip: str | None
    ) -> tuple[URL, bool]:
        """
        Returns (url_row, already_existed). The bool lets the route
        layer decide the HTTP status code (200 vs 201) without this
        method needing to know anything about HTTP.
        """
        self._reject_self_referential(long_url)

        # Content-based idempotency: same long URL, same result,
        # every time — see Milestone 5 discussion for why this is the
        # right idempotency model for this specific endpoint.
        existing = await self._repository.get_active_by_long_url(long_url)
        if existing is not None:
            # Refresh the TTL on repeat shortening too — a URL that's
            # actively being re-shortened is a signal it's likely to
            # be accessed soon, same "cache what's likely to be hot"
            # reasoning as cache warming on creation below.
            await self._cache.set(existing.short_code, existing.long_url, existing.expires_at)
            return existing, True

        # We don't know the short_code until we know the id, so we
        # explicitly reserve one from the sequence first, encode it,
        # then insert the fully-populated row in a single write.
        new_id = await self._repository.reserve_next_id()
        short_code = encode(new_id)

        url_row = await self._repository.create_with_id(
            id_=new_id,
            short_code=short_code,
            long_url=long_url,
            created_by_ip=created_by_ip,
        )

        # Cache warming (Milestone 1 concept, implemented here): a
        # freshly-created URL is statistically likely to be clicked
        # soon, so we populate Redis proactively instead of waiting
        # for the first redirect to pay the cache-miss cost.
        await self._cache.set(url_row.short_code, url_row.long_url, url_row.expires_at)

        urls_created_total.inc()
        return url_row, False

    async def resolve_for_redirect(self, short_code: str) -> RedirectTarget:
        """
        Cache-aside read path. Order of operations:

          1. Check Redis. Hit + not expired -> return immediately,
             Postgres is never touched. This is the ~99% steady-state
             path and the reason redirects stay fast under load.
          2. Hit but expired -> the cached data itself tells us this
             link is dead; raise URLGoneError without needing to ask
             Postgres to confirm something we already know, and clean
             up the now-useless cache entry.
          3. Miss -> fall back to Postgres, enforce is_active/expiry
             there, and on success warm the cache so the NEXT request
             for this code is a cache hit instead of another miss.
        """
        cached = await self._cache.get(short_code)
        if cached is not None:
            cache_hits_total.inc()
            if self._is_expired(cached.get("expires_at")):
                await self._cache.invalidate(short_code)
                redirects_total.labels(result="gone").inc()
                raise URLGoneError(f"Short code {short_code!r} expired (cache hit)")
            redirects_total.labels(result="success").inc()
            return RedirectTarget(long_url=cached["long_url"])

        cache_misses_total.inc()
        row = await self._repository.get_by_short_code_any_status(short_code)

        if row is None:
            redirects_total.labels(result="not_found").inc()
            raise URLNotFoundError(f"No URL found for short code {short_code!r}")

        if not row.is_active:
            redirects_total.labels(result="gone").inc()
            raise URLGoneError(f"Short code {short_code!r} has been deactivated")

        if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
            redirects_total.labels(result="gone").inc()
            raise URLGoneError(f"Short code {short_code!r} expired at {row.expires_at}")

        # Cache miss resolved successfully — warm it for next time.
        await self._cache.set(row.short_code, row.long_url, row.expires_at)

        redirects_total.labels(result="success").inc()
        return RedirectTarget(long_url=row.long_url)

    @staticmethod
    def _is_expired(expires_at_iso: str | None) -> bool:
        if expires_at_iso is None:
            return False
        expires_at = datetime.fromisoformat(expires_at_iso)
        return expires_at <= datetime.now(timezone.utc)

    def _reject_self_referential(self, long_url: str) -> None:
        host = urlparse(long_url).netloc
        if host == settings.base_host:
            raise SelfReferentialURLError(
                f"Cannot shorten a URL that points back at this service ({host})"
            )
