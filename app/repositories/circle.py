"""A user's circle on the server: who they may invite to a seat (one indexed query)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import CompoundSelect, Select, exists, literal, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, PlayerPhoto, User
from app.repositories.seats import seat_pairs


@dataclass(frozen=True)
class CircleRow:
    """A circle member with an account: the player, the account, and when each was last active."""

    user_id: uuid.UUID
    player_id: uuid.UUID
    name: str
    emoji: str
    color: str
    photo_rev: int
    has_photo: bool
    last_seen_at: datetime | None
    last_played_at: datetime | None


def circle_ids(
    user_id: uuid.UUID, player_id: uuid.UUID | None
) -> CompoundSelect[uuid.UUID] | Select[uuid.UUID]:
    """Player ids in the circle: players the user created, plus (with a player) everyone who
    shared a seated game with the user's player, and that player."""
    created = select(Player.id).where(Player.created_by == user_id)
    if player_id is None:
        return created
    mine = seat_pairs("mine")
    seats = seat_pairs("seats")
    shared = select(seats.c.player_id).where(
        seats.c.game_id.in_(select(mine.c.game_id).where(mine.c.player_id == player_id))
    )
    return union(created, shared, select(literal(player_id, Player.id.type)))


def members_stmt(user_id: uuid.UUID, player_id: uuid.UUID | None) -> Select[*tuple[Any, ...]]:
    """Invitable circle members (linked to an active account, never the user), last played first."""
    has_photo = exists().where(PlayerPhoto.player_id == Player.id)
    return (
        select(
            User.id,
            Player.id,
            Player.display_name,
            Player.emoji,
            Player.color,
            Player.photo_rev,
            has_photo,
            User.last_seen_at,
            Player.last_played_at,
        )
        .join(Player, Player.id == User.player_id)
        .where(
            User.player_id.in_(circle_ids(user_id, player_id)),
            User.id != user_id,
            User.active.is_(True),
            Player.merged_into.is_(None),
        )
        .order_by(Player.last_played_at.desc().nulls_last(), Player.display_name, Player.id)
    )


class CircleRepository:
    """Reads over a user's circle."""

    async def members(
        self, session: AsyncSession, user_id: uuid.UUID, player_id: uuid.UUID | None
    ) -> list[CircleRow]:
        """Every invitable member of the user's circle."""
        rows = await session.execute(members_stmt(user_id, player_id))
        return [CircleRow(*r) for r in rows]

    async def contains(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        player_id: uuid.UUID | None,
        candidate: uuid.UUID,
    ) -> bool:
        """Whether a player is in the user's circle."""
        ids = circle_ids(user_id, player_id)
        stmt = select(literal(candidate, Player.id.type).in_(ids))
        return bool((await session.execute(stmt)).scalar_one())


circle_repository = CircleRepository()
