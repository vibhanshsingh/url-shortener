"""
This is the concrete DI chain referenced back in Milestone 1: a route
declares `Depends(get_url_service)`, FastAPI resolves get_url_service,
which itself declares `Depends(get_db)` for a session, and builds a
repository and service around it. None of these layers construct their
own dependencies — everything is handed in, which is what makes it
trivial to override any layer in tests (e.g. swap get_db for a fixture
that yields a rollback-only test session).

Milestone 7 adds a second dependency branch (cache) alongside
repository — get_url_service now composes both, and URLService itself
doesn't know or care that one of its two collaborators talks to
Postgres and the other to Redis; it just calls methods on each.
"""

import redis.asyncio as redis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import get_redis_client
from app.cache.url_cache import URLCache
from app.core.database import get_db
from app.repository.url_repository import URLRepository
from app.services.url_service import URLService


def get_url_repository(session: AsyncSession = Depends(get_db)) -> URLRepository:
    return URLRepository(session)


def get_url_cache(client: redis.Redis = Depends(get_redis_client)) -> URLCache:
    return URLCache(client)


def get_url_service(
    repository: URLRepository = Depends(get_url_repository),
    cache: URLCache = Depends(get_url_cache),
) -> URLService:
    return URLService(repository, cache)
