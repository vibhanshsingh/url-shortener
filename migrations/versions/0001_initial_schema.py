"""initial schema: urls, url_stats, daily_stats, partitioned click_events

Revision ID: 0001
Revises:
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- urls ---
    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("short_code", sa.String(length=10), nullable=False),
        sa.Column("long_url", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by_ip", postgresql.INET(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("short_code"),
    )
    op.create_index(
        "idx_urls_short_code_active",
        "urls",
        ["short_code"],
        postgresql_where=sa.text("is_active = true"),
    )

    # --- url_stats (one-to-one with urls) ---
    op.create_table(
        "url_stats",
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("total_clicks", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_accessed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("url_id"),
    )

    # --- daily_stats ---
    op.create_table(
        "daily_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("click_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_id", "stat_date", name="uq_daily_stats_url_date"),
    )
    op.create_index("idx_daily_stats_url_date", "daily_stats", ["url_id", "stat_date"])

    # --- click_events: partitioned parent table ---
    # NOTE: primary key must include the partition key (clicked_at) —
    # Postgres enforces this for any unique constraint on a partitioned
    # table, including the PK itself.
    op.create_table(
        "click_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "clicked_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("device_type", sa.String(length=20), nullable=True),
        sa.Column("browser", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", "clicked_at"),
        postgresql_partition_by="RANGE (clicked_at)",
    )
    op.create_index(
        "idx_click_events_url_clicked_at", "click_events", ["url_id", "clicked_at"]
    )

    # Partition children can't be expressed through SQLAlchemy's Table
    # API — Postgres's "CREATE TABLE ... PARTITION OF ... FOR VALUES
    # FROM ... TO ..." syntax has no declarative ORM equivalent, so we
    # drop to raw SQL here. This creates the current and next month's
    # partitions so inserts work immediately after this migration runs.
    #
    # PRODUCTION NOTE: in Milestone 15 we'll replace this manual
    # two-partition bootstrap with either a scheduled job that creates
    # next month's partition ahead of time, or the pg_partman
    # extension, which automates this entirely. Without one of those,
    # inserts start failing the moment you cross into an unpartitioned
    # month — a real, easy-to-hit production bug if forgotten.
    op.execute(
        """
        CREATE TABLE click_events_2026_07 PARTITION OF click_events
        FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
        """
    )
    op.execute(
        """
        CREATE TABLE click_events_2026_08 PARTITION OF click_events
        FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS click_events_2026_08")
    op.execute("DROP TABLE IF EXISTS click_events_2026_07")
    op.drop_index("idx_click_events_url_clicked_at", table_name="click_events")
    op.drop_table("click_events")

    op.drop_index("idx_daily_stats_url_date", table_name="daily_stats")
    op.drop_table("daily_stats")

    op.drop_table("url_stats")

    op.drop_index("idx_urls_short_code_active", table_name="urls")
    op.drop_table("urls")
