"""
`daily_stats`: one row per (url, calendar day). This is the table that
powers the "daily clicks" breakdown in /stats/{code}, and it's small
enough (one row per URL per active day, not one row per click) to query
directly without partitioning.

The unique constraint on (url_id, stat_date) is what makes the
consumer's upsert logic correct: `INSERT ... ON CONFLICT (url_id,
stat_date) DO UPDATE SET click_count = click_count + 1` relies on this
constraint existing — without it, concurrent consumers could create
duplicate rows for the same URL/day instead of incrementing one row.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    click_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("url_id", "stat_date", name="uq_daily_stats_url_date"),
        # Supports "give me the last N days for this URL" ordered scans
        # without a sort step.
        Index("idx_daily_stats_url_date", "url_id", "stat_date"),
    )
