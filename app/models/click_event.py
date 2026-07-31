"""
`click_events`: the raw, immutable log of every redirect. This is the
table we partition by month on `clicked_at`, because it's the only
table whose row count scales with traffic rather than with URL count.

A real gotcha worth knowing for interviews: PostgreSQL requires the
partition key to be part of every unique constraint on a partitioned
table, including the primary key. That's why the primary key here is
the composite (id, clicked_at), not just id — a plain `id` PK would be
rejected by Postgres with "unique constraint must include partition
key" the moment you tried to create this as a partitioned table.

This table is never updated, only inserted into (by the Kafka
consumer) and read from (by the daily rollup process, and by ad-hoc
analytics queries that need detail daily_stats doesn't capture, like
"list the last 20 raw referrers").
"""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import INET, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=False, nullable=False
    )
    # Part of the composite primary key — see module docstring for why.
    clicked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), primary_key=True, nullable=False
    )

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    device_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        # Serves "all clicks for this URL in a date range" — the query
        # both the rollup job and any ad-hoc analytics will run.
        Index("idx_click_events_url_clicked_at", "url_id", "clicked_at"),
        {"postgresql_partition_by": "RANGE (clicked_at)"},
    )
