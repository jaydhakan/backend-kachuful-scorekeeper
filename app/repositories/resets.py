"""Password reset links."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PasswordReset


class ResetsRepository:
    """Data access for `password_resets`."""

    async def create(
        self, session: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> PasswordReset:
        """Insert a reset link."""
        reset = PasswordReset(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        session.add(reset)
        await session.flush()
        return reset

    async def get_usable(
        self, session: AsyncSession, token_hash: str, now: datetime
    ) -> PasswordReset | None:
        """The reset for a token if unused and unexpired."""
        stmt = select(PasswordReset).where(
            PasswordReset.token_hash == token_hash,
            PasswordReset.used_at.is_(None),
            PasswordReset.expires_at > now,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def mark_used(self, session: AsyncSession, reset: PasswordReset, at: datetime) -> None:
        """Use up a reset link."""
        reset.used_at = at
        await session.flush()


resets_repository = ResetsRepository()
