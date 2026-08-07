"""
Plain-language version of what this file does:

- record_click(...): every time someone clicks a short link, call this
  once. It bumps the "total clicks" number up by one, and also bumps
  up "today's click count" by one. If today doesn't have a row yet,
  it creates one starting at 1.

- get_totals(...): the fast "how many clicks total, when was it last
  clicked" answer — reads one small row, no counting required.

- get_daily_clicks(...): "how many clicks per day for the last 30
  days" — reads the small daily_stats table, not the giant raw log.

- get_breakdown(...): "how many clicks came from each country /
  browser / device" — this ONE does have to look through the raw
  click_events log, because we never pre-counted those breakdowns.
  That's a deliberate trade-off: we only pre-aggregate the numbers we
  know we'll need constantly (totals, daily counts). Everything else
  is computed on the spot, which is fine since /stats is checked far
  less often than a redirect happens.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.click_event import ClickEvent
from app.models.daily_stats import DailyStats
from app.models.url_stats import URLStats


class StatsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def record_click(self, url_id: int, clicked_at: datetime, commit: bool = True) -> None:
        """
        Call this once per click. It's the "upsert" described above,
        run twice: once for the all-time total, once for today's
        count. Both use the exact same trick — "add one, or start at
        one if this is the first entry."

        commit=False lets a caller bundle this with another write (the
        consumer does exactly this — it wants "save the raw click" AND
        "update the totals" to succeed or fail together, as one unit,
        not as two separate commits that could get out of sync if the
        process crashes between them).
        """
        stat_date = clicked_at.date()

        # Bump the all-time total. This row already exists for every
        # URL (created back in Milestone 5 alongside the URL itself),
        # so in practice this always takes the "update" branch — but
        # writing it as an upsert means it can never fail even if that
        # assumption is ever wrong.
        url_stats_stmt = pg_insert(URLStats).values(
            url_id=url_id, total_clicks=1, last_accessed_at=clicked_at
        )
        url_stats_stmt = url_stats_stmt.on_conflict_do_update(
            index_elements=["url_id"],
            set_={
                "total_clicks": URLStats.total_clicks + 1,
                "last_accessed_at": clicked_at,
            },
        )
        await self._session.execute(url_stats_stmt)

        # Bump today's count. First click of the day creates the row
        # at 1; every click after that just adds 1.
        daily_stats_stmt = pg_insert(DailyStats).values(
            url_id=url_id, stat_date=stat_date, click_count=1
        )
        daily_stats_stmt = daily_stats_stmt.on_conflict_do_update(
            index_elements=["url_id", "stat_date"],
            set_={"click_count": DailyStats.click_count + 1},
        )
        await self._session.execute(daily_stats_stmt)

        if commit:
            await self._session.commit()

    async def get_totals(self, url_id: int) -> URLStats | None:
        """The fast answer: total clicks, last clicked when. One small
        row, no counting."""
        stmt = select(URLStats).where(URLStats.url_id == url_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_daily_clicks(self, url_id: int, days: int = 30) -> list[DailyStats]:
        """Last N days of click counts, newest first."""
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(DailyStats)
            .where(DailyStats.url_id == url_id, DailyStats.stat_date >= cutoff)
            .order_by(DailyStats.stat_date.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_breakdown(self, url_id: int, column, limit: int = 10) -> list[tuple[str, int]]:
        """
        Generic "count clicks grouped by X" helper — used for country,
        browser, and device breakdowns below. `column` is one of the
        ClickEvent model's columns, e.g. ClickEvent.browser.

        This is the one query in this file that scans click_events
        directly rather than reading a pre-aggregated table — see the
        module docstring for why that's an acceptable trade-off here.
        """
        stmt = (
            select(column, func.count().label("count"))
            .where(ClickEvent.url_id == url_id, column.isnot(None))
            .group_by(column)
            .order_by(func.count().desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]
