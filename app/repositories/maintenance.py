"""Housekeeping deletes for the prune job."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, PasswordReset, RateLimit, Session

# Used invites and resets stay this long, so the admin screen can still explain them.
KEEP_USED = timedelta(days=30)
# Rate-limit windows are at most 10 minutes; anything older than a day is finished.
KEEP_WINDOWS = timedelta(days=1)


@dataclass(frozen=True)
class Pruned:
    """How many rows each table lost."""

    sessions: int
    invites: int
    resets: int
    rate_limits: int


class MaintenanceRepository:
    """Deletes rows nobody can use any more."""

    async def prune(self, session: AsyncSession, now: datetime) -> Pruned:
        """Expired sessions, dead invites and resets, and finished rate-limit windows."""
        old = now - KEEP_USED
        sessions = await session.execute(
            delete(Session).where(Session.expires_at <= now).returning(Session.id)
        )
        invites = await session.execute(
            delete(Invite)
            .where(
                or_(
                    and_(Invite.accepted_at.is_(None), Invite.expires_at <= now),
                    Invite.accepted_at <= old,
                    Invite.revoked_at <= old,
                )
            )
            .returning(Invite.id)
        )
        resets = await session.execute(
            delete(PasswordReset)
            .where(or_(PasswordReset.expires_at <= old, PasswordReset.used_at <= old))
            .returning(PasswordReset.id)
        )
        windows = await session.execute(
            delete(RateLimit)
            .where(RateLimit.window_start <= now - KEEP_WINDOWS)
            .returning(RateLimit.key)
        )
        return Pruned(
            sessions=len(sessions.all()),
            invites=len(invites.all()),
            resets=len(resets.all()),
            rate_limits=len(windows.all()),
        )


maintenance_repository = MaintenanceRepository()
