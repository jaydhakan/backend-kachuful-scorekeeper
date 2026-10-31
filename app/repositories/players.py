"""The player registry."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Player, User


@dataclass(frozen=True)
class PlayerView:
    """A player with what the contract's Player and PlayerSummary add from other tables."""

    player: Player
    linked_user_id: uuid.UUID | None
    created_by_name: str


_linked = aliased(User)
_creator = aliased(User)
_creator_player = aliased(Player)


def _view_query() -> Select[Player, uuid.UUID | None, str]:
    return (
        select(
            Player,
            _linked.id,
            func.coalesce(_creator_player.display_name, ""),
        )
        .outerjoin(_linked, _linked.player_id == Player.id)
        .outerjoin(_creator, _creator.id == Player.created_by)
        .outerjoin(_creator_player, _creator_player.id == _creator.player_id)
    )


def _to_views(rows: Sequence[Any]) -> list[PlayerView]:
    return [PlayerView(player=r[0], linked_user_id=r[1], created_by_name=r[2]) for r in rows]


class PlayersRepository:
    """Data access for `players`."""

    async def get(self, session: AsyncSession, player_id: uuid.UUID) -> Player | None:
        """A player by id."""
        return await session.get(Player, player_id)

    async def get_for_update(self, session: AsyncSession, player_id: uuid.UUID) -> Player | None:
        """A player by id, row-locked for the rest of the transaction."""
        stmt = select(Player).where(Player.id == player_id).with_for_update()
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_many(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Player]:
        """Players by id, in one query."""
        if not player_ids:
            return {}
        stmt = select(Player).where(Player.id.in_(set(player_ids)))
        return {p.id: p for p in (await session.execute(stmt)).scalars()}

    async def get_view(self, session: AsyncSession, player_id: uuid.UUID) -> PlayerView | None:
        """One player with link and creator name."""
        views = await self.get_views(session, [player_id])
        return views.get(player_id)

    async def get_views(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PlayerView]:
        """Players with link and creator name, in one query."""
        if not player_ids:
            return {}
        stmt = _view_query().where(Player.id.in_(set(player_ids)))
        return {v.player.id: v for v in _to_views((await session.execute(stmt)).all())}

    async def list_views(
        self,
        session: AsyncSession,
        *,
        updated_since: datetime | None,
        after: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[PlayerView]:
        """Registry page in sync order (updated_at, id), merged players included."""
        stmt = _view_query().order_by(Player.updated_at, Player.id).limit(limit)
        if updated_since is not None:
            stmt = stmt.where(Player.updated_at > updated_since)
        if after is not None:
            at, last_id = after
            stmt = stmt.where(
                or_(Player.updated_at > at, and_(Player.updated_at == at, Player.id > last_id))
            )
        return _to_views((await session.execute(stmt)).all())

    async def list_all_views(self, session: AsyncSession) -> list[PlayerView]:
        """The whole registry (bootstrap)."""
        stmt = _view_query().order_by(Player.updated_at, Player.id)
        return _to_views((await session.execute(stmt)).all())

    async def get_active_by_name_key(self, session: AsyncSession, key: str) -> Player | None:
        """The unmerged player holding a name key."""
        stmt = select(Player).where(Player.name_key == key, Player.merged_into.is_(None))
        return (await session.execute(stmt)).scalar_one_or_none()

    async def create(self, session: AsyncSession, player: Player) -> Player:
        """Insert a player (the caller has checked the name)."""
        session.add(player)
        await session.flush()
        await session.refresh(player)
        return player

    async def touch(self, session: AsyncSession, player: Player) -> None:
        """Bump updated_at so sync clients pick the change up."""
        player.updated_at = func.now()
        await session.flush()
        await session.refresh(player)

    async def redirect_merged(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Mark a player merged, and repoint players already merged into it."""
        now = func.now()
        await session.execute(
            update(Player)
            .where(Player.merged_into == from_id)
            .values(merged_into=into_id, updated_at=now)
        )
        await session.execute(
            update(Player).where(Player.id == from_id).values(merged_into=into_id, updated_at=now)
        )


players_repository = PlayersRepository()
