"""FastAPI dependencies: the request's DB session and one factory per service."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services.core import CoreService


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request; services commit, anything uncommitted is rolled back."""
    async with get_session_factory()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_core_service() -> CoreService:
    """CoreService for a request."""
    return CoreService()
