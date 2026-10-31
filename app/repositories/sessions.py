"""Browser sessions."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Session, User


class SessionsRepository:
    """Data access for `sessions`."""

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
    ) -> Session:
        """Insert a session."""
        row = Session(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at, user_agent=user_agent
        )
        session.add(row)
        await session.flush()
        return row

    async def get_live_with_user(
        self, session: AsyncSession, token_hash: str, now: datetime
    ) -> tuple[Session, User] | None:
        """An unexpired session and its user, in one query."""
        stmt = (
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == token_hash, Session.expires_at > now)
        )
        row = (await session.execute(stmt)).one_or_none()
        return None if row is None else (row[0], row[1])

    async def refresh(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        *,
        last_used_at: datetime,
        expires_at: datetime | None,
    ) -> None:
        """Record use, and extend the expiry when given."""
        values: dict[str, datetime] = {"last_used_at": last_used_at}
        if expires_at is not None:
            values["expires_at"] = expires_at
        await session.execute(update(Session).where(Session.id == session_id).values(**values))

    async def delete(self, session: AsyncSession, session_id: uuid.UUID) -> None:
        """Delete one session."""
        await session.execute(delete(Session).where(Session.id == session_id))

    async def delete_for_user(
        self, session: AsyncSession, user_id: uuid.UUID, *, keep: uuid.UUID | None = None
    ) -> None:
        """Delete a user's sessions, optionally keeping one."""
        stmt = delete(Session).where(Session.user_id == user_id)
        if keep is not None:
            stmt = stmt.where(Session.id != keep)
        await session.execute(stmt)


sessions_repository = SessionsRepository()
