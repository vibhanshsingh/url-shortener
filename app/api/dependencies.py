"""
This is the concrete DI chain referenced back in Milestone 1: a route
declares `Depends(get_url_service)`, FastAPI resolves get_url_service,
which itself declares `Depends(get_db)` for a session, and builds a
repository and service around it. None of these layers construct their
own dependencies — everything is handed in, which is what makes it
trivial to override any layer in tests (e.g. swap get_db for a fixture
that yields a rollback-only test session).
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repository.url_repository import URLRepository
from app.services.url_service import URLService


def get_url_repository(session: AsyncSession = Depends(get_db)) -> URLRepository:
    return URLRepository(session)


def get_url_service(repository: URLRepository = Depends(get_url_repository)) -> URLService:
    return URLService(repository)
