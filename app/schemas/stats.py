"""
Plain-language shape of the answer: "here's the total, here's when it
was made and last clicked, and here's a simple breakdown by day,
country, browser, and device."
"""

from datetime import date, datetime

from pydantic import BaseModel


class DailyClickCount(BaseModel):
    date: date
    clicks: int


class BreakdownItem(BaseModel):
    label: str
    clicks: int


class StatsResponse(BaseModel):
    short_code: str
    long_url: str
    created_at: datetime
    total_clicks: int
    last_accessed_at: datetime | None
    daily_clicks: list[DailyClickCount]
    by_country: list[BreakdownItem]
    by_browser: list[BreakdownItem]
    by_device: list[BreakdownItem]
