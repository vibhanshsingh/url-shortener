"""
Async engine + session factory.

Why async SQLAlchemy specifically: FastAPI's whole performance story is
built on async I/O — if we used the sync SQLAlchemy driver, every DB
call would block the event loop, and we'd lose the ability to serve
other requests while waiting on Postgres. That defeats the point of
choosing FastAPI in the first place.

Why a connection pool with explicit sizing: without bounds, under load
the app would try to open unlimited connections to Postgres, which has
a hard connection limit (default 100). pool_size + max_overflow cap
how many connections this single API instance can hold, so that when
we scale to N instances (Milestone "why do we need a load balancer"),
N x pool_size stays under Postgres's ceiling — sizing this correctly is
a real production incident category, not a hypothetical.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# asyncpg is the driver; "postgresql+asyncpg://" tells SQLAlchemy which
# DBAPI to use under the async engine.
ASYNC_DATABASE_URL = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,  # detects a dead connection before using it, not after
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency. `Depends(get_db)` in a route hands the route a
    session and guarantees it's closed after the request, even if the
    handler raises. This is the DI pattern from Milestone 1's
    walkthrough, applied to the database specifically.
    """
    async with AsyncSessionLocal() as session:
        yield session
