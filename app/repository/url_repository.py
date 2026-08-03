"""
Repository pattern (recap from Milestone 1): this is the only layer
that knows SQLAlchemy exists. The service layer calls plain methods
like `create()` and `get_active_by_long_url()` without knowing or
caring whether that's backed by Postgres, a read replica, or something
else entirely — which is exactly what lets us swap in a read replica
later (Milestone 15) by editing this one file.
"""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.url import URL
from app.models.url_stats import URLStats


class URLRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_active_by_long_url(self, long_url: str) -> URL | None:
        """
        Backs the content-based idempotency check: has this exact URL
        already been shortened (and not soft-deleted)?
        """
        stmt = select(URL).where(URL.long_url == long_url, URL.is_active == True)  # noqa: E712
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_short_code(self, short_code: str) -> URL | None:
        """Only returns active, non-deleted URLs. Used where 'does an
        active URL with this code exist' is the exact question — e.g.
        rejecting a newly-created code that collides with something
        still live."""
        stmt = select(URL).where(URL.short_code == short_code, URL.is_active == True)  # noqa: E712
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_short_code_any_status(self, short_code: str) -> URL | None:
        """
        Returns a URL row regardless of is_active — including
        soft-deleted ones. The redirect endpoint needs this specific
        distinction: a short code that was NEVER created should be a
        404, but one that existed and was later deactivated or expired
        should be a 410 Gone. Filtering by is_active here would
        collapse both cases into an indistinguishable "not found."
        """
        stmt = select(URL).where(URL.short_code == short_code)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def reserve_next_id(self) -> int:
        """
        Pulls the next value directly from urls_id_seq — the sequence
        backing the BIGSERIAL column — without inserting a row yet.
        This is what lets the service layer encode the Base62 code
        BEFORE the insert, so create_with_id() below can do a single
        INSERT with every column already populated, instead of
        insert-then-update.

        Note: BIGSERIAL's default sequence name follows Postgres's
        convention "<table>_<column>_seq" — hence "urls_id_seq".

        Public (not prefixed with _) because the service layer must
        call this directly to get the id to encode — it's a real part
        of this repository's contract, not an internal detail.
        """
        result = await self._session.execute(text("SELECT nextval('urls_id_seq')"))
        return result.scalar_one()

    async def create_with_id(
        self, *, id_: int, short_code: str, long_url: str, created_by_ip: str | None
    ) -> URL:
        """
        Inserts the URL row AND its corresponding url_stats row in the
        same transaction, using an id already reserved via
        reserve_next_id(). A URL should never exist without a stats
        row — creating them together, atomically, means the analytics
        endpoint in Milestone 9 never has to handle a "stats row is
        missing" case as a special path.
        """
        url_row = URL(
            id=id_,
            short_code=short_code,
            long_url=long_url,
            created_by_ip=created_by_ip,
        )
        stats_row = URLStats(url_id=id_, total_clicks=0)

        self._session.add(url_row)
        self._session.add(stats_row)
        await self._session.commit()
        await self._session.refresh(url_row)

        return url_row
