"""Users."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User


class UsersRepository:
    """Data access for `users`."""

    async def get(self, session: AsyncSession, user_id: uuid.UUID) -> User | None:
        """A user by id."""
        return await session.get(User, user_id)

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        """A user by email (case-insensitive: citext)."""
        return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    async def get_by_player(self, session: AsyncSession, player_id: uuid.UUID) -> User | None:
        """The user linked to a player, if any."""
        stmt = select(User).where(User.player_id == player_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, session: AsyncSession) -> list[User]:
        """Every user, oldest first."""
        return list((await session.execute(select(User).order_by(User.created_at))).scalars())

    async def create(
        self,
        session: AsyncSession,
        *,
        email: str,
        role: str,
        password_hash: str | None,
        player_id: uuid.UUID | None,
    ) -> User:
        """Insert a user and return it."""
        user = User(email=email, role=role, password_hash=password_hash, player_id=player_id)
        session.add(user)
        await session.flush()
        await session.refresh(user)
        return user

    async def set_password(self, session: AsyncSession, user: User, password_hash: str) -> None:
        """Store a new password hash."""
        user.password_hash = password_hash
        await session.flush()

    async def touch_last_seen(
        self, session: AsyncSession, user_id: uuid.UUID, at: datetime
    ) -> None:
        """Record activity."""
        await session.execute(update(User).where(User.id == user_id).values(last_seen_at=at))

    async def merge_preferences(
        self, session: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any]
    ) -> User:
        """Overwrite the given preference keys in one atomic update; returns the fresh row."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                preferences=User.preferences.op("||")(literal(changes, JSONB)),
                preferences_updated_at=func.now(),
            )
            .returning(User)
            .execution_options(populate_existing=True)
        )
        return (await session.execute(stmt)).scalar_one()

    async def count_active_admins(self, session: AsyncSession) -> int:
        """How many active admins there are."""
        stmt = select(func.count()).where(User.role == "admin", User.active.is_(True))
        return (await session.execute(stmt)).scalar_one()

    async def move_player_link(
        self, session: AsyncSession, from_player: uuid.UUID, to_player: uuid.UUID
    ) -> None:
        """Point the user linked to one player at another (player merge)."""
        await session.execute(
            update(User).where(User.player_id == from_player).values(player_id=to_player)
        )

    async def counts(self, session: AsyncSession) -> tuple[int, int]:
        """(users, unmerged players)."""
        users = select(func.count()).select_from(User).scalar_subquery()
        players = (
            select(func.count()).select_from(Player).where(Player.merged_into.is_(None))
        ).scalar_subquery()
        user_count, player_count = (await session.execute(select(users, players))).one()
        return int(user_count), int(player_count)


users_repository = UsersRepository()
