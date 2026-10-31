"""The one-row app_state table: data version and last backup."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppState


class AppStateRepository:
    """Reads and bumps the global data version."""

    async def data_version(self, session: AsyncSession) -> int:
        """The current data version (one primary-key read)."""
        return (await session.execute(select(AppState.data_version))).scalar_one()

    async def bump(self, session: AsyncSession) -> int:
        """Increment the data version after a write that changes what readers see."""
        stmt = (
            update(AppState)
            .values(data_version=AppState.data_version + 1, updated_at=func.now())
            .returning(AppState.data_version)
        )
        return (await session.execute(stmt)).scalar_one()

    async def last_backup_at(self, session: AsyncSession) -> datetime | None:
        """When the last backup finished."""
        return (await session.execute(select(AppState.last_backup_at))).scalar_one()

    async def set_last_backup_at(self, session: AsyncSession, at: datetime) -> None:
        """Record a finished backup."""
        await session.execute(update(AppState).values(last_backup_at=at))


app_state_repository = AppStateRepository()
