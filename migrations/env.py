"""
Alembic normally assumes a sync engine. Since our app uses async
SQLAlchemy everywhere else, we run migrations through the async engine
too — using `connection.run_sync(...)` to bridge Alembic's sync API
into our async engine. This is the standard pattern for async
SQLAlchemy + Alembic; without it you'd need a second, sync-only
database URL just for migrations, which is one more thing to keep in
sync (pun intended) with the real config.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import engine
from app.models import Base  # noqa: F401 — imports all models onto Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic compares the live DB schema against this to autogenerate
# migrations. Because app/models/__init__.py imports every model,
# Base.metadata here is guaranteed complete.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generates SQL scripts without a live DB connection (rarely used
    here, but standard Alembic scaffolding — e.g. for DBA review workflows
    where migrations are generated as SQL and reviewed before running)."""
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable: AsyncEngine = engine
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
