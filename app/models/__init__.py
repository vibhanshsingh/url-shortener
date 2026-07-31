"""
Alembic's autogenerate compares the live database against
Base.metadata. If a model module is never imported, its table never
registers itself on Base.metadata, and Alembic will silently propose
to DROP a table that actually exists just fine — a classic and
confusing bug. Importing every model here, once, in one place, is the
fix: migrations/env.py imports this package and gets the full picture.
"""

from app.models.base import Base
from app.models.click_event import ClickEvent
from app.models.daily_stats import DailyStats
from app.models.url import URL
from app.models.url_stats import URLStats

__all__ = ["Base", "URL", "URLStats", "DailyStats", "ClickEvent"]
