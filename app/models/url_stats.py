"""
`url_stats`: one row per URL, holding running totals.

Why this exists separately from daily_stats: /stats/{code} needs an
instant "total clicks, last accessed" answer without summing across
every daily_stats row for that URL (which grows unbounded over the
URL's lifetime). This table is the O(1)-read answer to "how many
clicks total" — updated incrementally by the Kafka consumer alongside
daily_stats, in the same logical operation.

url_id is both the primary key and a foreign key — this enforces the
one-to-one relationship at the schema level, not just by convention.
"""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class URLStats(Base):
    __tablename__ = "url_stats"

    url_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("urls.id", ondelete="CASCADE"),
        primary_key=True,
    )
    total_clicks: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
