"""add unique constraint on urls.long_url to close the concurrent-creation race

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08

Plain-language version: back in Milestone 5, our idempotency check
worked like this: "look up the long URL; if it exists, return it;
otherwise create a new row." Under normal traffic that's fine. But if
TWO identical requests arrive at almost the exact same instant, both
can run that lookup before EITHER has finished creating its row — both
see "doesn't exist yet" and both proceed to create one. Two different
short codes now point at the same long URL, which breaks the
idempotency promise we made.

A unique constraint makes this impossible at the database level: no
matter how close together two inserts happen, Postgres itself
guarantees only one can succeed. The application catches the resulting
conflict and re-fetches the winning row — see
app/services/url_service.py for the handling side of this fix.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_urls_long_url", "urls", ["long_url"])


def downgrade() -> None:
    op.drop_constraint("uq_urls_long_url", "urls", type_="unique")
