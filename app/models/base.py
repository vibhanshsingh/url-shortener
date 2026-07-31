"""
Every model inherits from this Base. Why a separate file instead of
defining Base inside models/url.py: Alembic's env.py needs to import
`Base.metadata` without pulling in a random model file, and every model
file needs to import Base without circular imports. One shared, neutral
home for it.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
