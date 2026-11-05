"""Invites (one-time account links)."""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite


def _pending() -> tuple[ColumnElement[bool], ...]:
    return (Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))


class InvitesRepository:
    """Data access for `invites`."""

    async def create(
        self,
        session: AsyncSession,
        *,
        email: str,
        token_hash: str,
        player_id: uuid.UUID | None,
        created_by: uuid.UUID | None,
        expires_at: datetime,
    ) -> Invite:
        """Insert an invite."""
        invite = Invite(
            email=email,
            token_hash=token_hash,
            player_id=player_id,
            created_by=created_by,
            expires_at=expires_at,
        )
        session.add(invite)
        await session.flush()
        return invite

    async def revoke_pending(self, session: AsyncSession, email: str, at: datetime) -> int:
        """Revoke every pending invite for an email; returns how many."""
        stmt = (
            update(Invite)
            .where(Invite.email == email, *_pending())
            .values(revoked_at=at)
            .returning(Invite.id)
        )
        return len((await session.execute(stmt)).all())

    async def get_usable(
        self, session: AsyncSession, token_hash: str, now: datetime
    ) -> Invite | None:
        """The invite for a token if it is pending and unexpired."""
        stmt = select(Invite).where(
            Invite.token_hash == token_hash, Invite.expires_at > now, *_pending()
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_usable(self, session: AsyncSession, now: datetime) -> list[Invite]:
        """Every pending, unexpired invite, newest first."""
        stmt = (
            select(Invite)
            .where(Invite.expires_at > now, *_pending())
            .order_by(Invite.created_at.desc())
        )
        return list((await session.execute(stmt)).scalars())

    async def count_expiring(self, session: AsyncSession, now: datetime, until: datetime) -> int:
        """Pending invites that are still usable at `now` but expire before `until`."""
        stmt = (
            select(func.count())
            .select_from(Invite)
            .where(*_pending(), Invite.expires_at > now, Invite.expires_at <= until)
        )
        return int((await session.execute(stmt)).scalar_one())

    async def mark_accepted(self, session: AsyncSession, invite: Invite, at: datetime) -> None:
        """Use up an invite."""
        invite.accepted_at = at
        await session.flush()


invites_repository = InvitesRepository()
