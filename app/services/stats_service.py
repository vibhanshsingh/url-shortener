"""
This class does one job: given a short code, go collect all the pieces
of its stats page and hand back one clean answer. It doesn't know
about HTTP — same rule as URLService (Milestone 1) — so it's testable
on its own, the same way.
"""

from app.models.click_event import ClickEvent
from app.repository.stats_repository import StatsRepository
from app.repository.url_repository import URLRepository
from app.schemas.stats import BreakdownItem, DailyClickCount, StatsResponse
from app.services.url_service import URLNotFoundError


class StatsService:
    def __init__(self, url_repository: URLRepository, stats_repository: StatsRepository):
        self._url_repository = url_repository
        self._stats_repository = stats_repository

    async def get_stats(self, short_code: str) -> StatsResponse:
        # Reuse the same "does this code exist at all" lookup from the
        # redirect flow — no need for a second way of answering the
        # same question. We deliberately don't reject deactivated or
        # expired links here the way redirect does: someone should
        # still be able to see the stats for an old link, even one
        # that no longer redirects.
        url_row = await self._url_repository.get_by_short_code_any_status(short_code)
        if url_row is None:
            raise URLNotFoundError(f"No URL found for short code {short_code!r}")

        totals = await self._stats_repository.get_totals(url_row.id)
        daily_rows = await self._stats_repository.get_daily_clicks(url_row.id)

        country_rows = await self._stats_repository.get_breakdown(url_row.id, ClickEvent.country)
        browser_rows = await self._stats_repository.get_breakdown(url_row.id, ClickEvent.browser)
        device_rows = await self._stats_repository.get_breakdown(url_row.id, ClickEvent.device_type)

        return StatsResponse(
            short_code=url_row.short_code,
            long_url=url_row.long_url,
            created_at=url_row.created_at,
            # totals can technically be None if a URL somehow has no
            # url_stats row — shouldn't happen given Milestone 5
            # creates both together, but zero is a safer default than
            # letting a 500 error leak out of a stats page.
            total_clicks=totals.total_clicks if totals else 0,
            last_accessed_at=totals.last_accessed_at if totals else None,
            daily_clicks=[
                DailyClickCount(date=row.stat_date, clicks=row.click_count) for row in daily_rows
            ],
            by_country=[BreakdownItem(label=label, clicks=count) for label, count in country_rows],
            by_browser=[BreakdownItem(label=label, clicks=count) for label, count in browser_rows],
            by_device=[BreakdownItem(label=label, clicks=count) for label, count in device_rows],
        )
