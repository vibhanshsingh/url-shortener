"""
Service layer (recap from Milestone 1): business logic, no HTTP
concerns. This class doesn't know it's being called from a FastAPI
route — it could just as easily be called from a CLI script or a
background job, which is exactly why it's testable without spinning up
a test HTTP client.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from app.core.config import settings
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


class URLService:
    def __init__(self, repository: URLRepository):
        self._repository = repository

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
        return url_row, False

    async def resolve_for_redirect(self, short_code: str) -> URL:
        """
        Looks up a short code for redirect purposes and enforces the
        business rules around expiry/deactivation. Raises
        URLNotFoundError or URLGoneError rather than returning None,
        so the route layer's error handling is a simple try/except
        instead of a chain of `if row is None` / `if not row.is_active`
        checks re-derived at the HTTP layer.
        """
        row = await self._repository.get_by_short_code_any_status(short_code)

        if row is None:
            raise URLNotFoundError(f"No URL found for short code {short_code!r}")

        if not row.is_active:
            raise URLGoneError(f"Short code {short_code!r} has been deactivated")

        if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
            raise URLGoneError(f"Short code {short_code!r} expired at {row.expires_at}")

        return row

    def _reject_self_referential(self, long_url: str) -> None:
        host = urlparse(long_url).netloc
        if host == settings.base_host:
            raise SelfReferentialURLError(
                f"Cannot shorten a URL that points back at this service ({host})"
            )
