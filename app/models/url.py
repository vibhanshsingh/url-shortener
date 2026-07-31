"""
The `urls` table: source of truth for the short_code -> long_url mapping.

Design decisions (recap from Milestone 1 discussion):
- id is the BIGSERIAL that Base62-encodes into short_code. We insert
  first, encode second — see app/services/encoding.py in Milestone 4.
- short_code is denormalized (stored, not computed on read) so lookups
  are a plain indexed string match, not a decode operation per request.
- is_active is a soft-delete flag. We never hard-delete a URL, because
  click_events and daily_stats reference it and we don't want to lose
  analytics history or force a cascading data loss.
- The partial index only covers active rows, since ~100% of redirect
  traffic queries WHERE is_active = true.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import INET, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class URL(Base):
    __tablename__ = "urls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    short_code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    long_url: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)

    __table_args__ = (
        # Partial index: only active rows, since that's ~100% of redirect
        # query traffic. Smaller index, faster lookups, faster writes.
        Index(
            "idx_urls_short_code_active",
            "short_code",
            postgresql_where=(is_active == True),  # noqa: E712
        ),
    )
