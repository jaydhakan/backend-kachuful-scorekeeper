"""Ishaara games: the locked game row, its key, seats, actions, lists and idle games."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Game, IshaaraAction, IshaaraKey, IshaaraRole, Player, User
from app.domain.enums import GameKind

KIND = GameKind.ISHAARA
CLUE_GIVER = "clue-giver"


@dataclass(frozen=True)
class IshaaraListRow:
    """What an IshaaraSummary is built from: the stored summary, host name and change time."""

    game_id: uuid.UUID
    summary: dict[str, Any]
    host_name: str
    updated_at: datetime


@dataclass(frozen=True)
class ListScope:
    """Which games a list reads: the caller's own (unless `everyone`), then the filters."""

    user_id: uuid.UUID
    player_id: uuid.UUID | None
    everyone: bool
    clue_giver: bool
    status: str | None
    involving: uuid.UUID | None


_host = aliased(User)
_host_player = aliased(Player)


def _seat_of(player_id: uuid.UUID) -> ColumnElement[bool]:
    return exists().where(IshaaraRole.game_id == Game.id, IshaaraRole.player_id == player_id)


def _giving_clues(user_id: uuid.UUID) -> ColumnElement[bool]:
    return exists().where(
        IshaaraRole.game_id == Game.id,
        IshaaraRole.user_id == user_id,
        IshaaraRole.role == CLUE_GIVER,
    )


def _scope_filters(scope: ListScope) -> list[ColumnElement[bool]]:
    """Hosted or seated (or clue-giving) unless `everyone`; then role, status and player filters."""
    filters: list[ColumnElement[bool]] = []
    if not scope.everyone:
        mine = [Game.owner_id == scope.user_id, _giving_clues(scope.user_id)]
        if scope.player_id is not None:
            mine.append(_seat_of(scope.player_id))
        filters.append(or_(*mine))
    if scope.clue_giver:
        filters.append(_giving_clues(scope.user_id))
    if scope.status is not None:
        filters.append(Game.status == scope.status)
    if scope.involving is not None:
        filters.append(_seat_of(scope.involving))
    return filters


class IshaaraRepository:
    """Data access for Ishaara's games row, key, roles and actions."""

    async def lock_row(self, session: AsyncSession, game_id: uuid.UUID) -> Game | None:
        """The game row of any kind, locked FOR UPDATE until the transaction ends."""
        stmt = (
            select(Game)
            .where(Game.id == game_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_row(self, session: AsyncSession, game_id: uuid.UUID) -> Game | None:
        """An Ishaara game row (deleted ones included), unlocked."""
        row = await session.get(Game, game_id, populate_existing=True)
        return row if row is not None and row.kind == KIND else None

    async def payload(self, session: AsyncSession, game_id: uuid.UUID) -> dict[str, Any] | None:
        """A live Ishaara game's public state (the live hub's one read per change)."""
        stmt = select(Game.payload).where(
            Game.id == game_id, Game.kind == KIND, Game.deleted_at.is_(None)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def insert_game(
        self,
        session: AsyncSession,
        row: Game,
        key: list[str],
        roles: Sequence[dict[str, Any]],
    ) -> None:
        """A new game with its key and seats."""
        session.add(row)
        await session.flush()
        await session.execute(insert(IshaaraKey).values(game_id=row.id, key=key))
        await session.execute(insert(IshaaraRole), [{"game_id": row.id, **r} for r in roles])

    async def save(self, session: AsyncSession, row: Game) -> None:
        """Flush a changed game row."""
        session.add(row)
        await session.flush()

    async def key(self, session: AsyncSession, game_id: uuid.UUID) -> list[str]:
        """The game's key (only the key endpoint, the action service and finishing call this)."""
        stmt = select(IshaaraKey.key).where(IshaaraKey.game_id == game_id)
        return list((await session.execute(stmt)).scalar_one())

    async def roles(self, session: AsyncSession, game_id: uuid.UUID) -> list[IshaaraRole]:
        """Every seat of a game."""
        stmt = select(IshaaraRole).where(IshaaraRole.game_id == game_id)
        return list((await session.execute(stmt)).scalars())

    async def clue_teams(
        self, session: AsyncSession, user_id: uuid.UUID, game_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """game → the team this account gives clues for, over some games (one query)."""
        if not game_ids:
            return {}
        stmt = select(IshaaraRole.game_id, IshaaraRole.team).where(
            IshaaraRole.user_id == user_id,
            IshaaraRole.role == CLUE_GIVER,
            IshaaraRole.game_id.in_(game_ids),
        )
        return {r[0]: r[1] for r in await session.execute(stmt)}

    async def delete_roles(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a game's seats (a deleted game sits nobody)."""
        await session.execute(delete(IshaaraRole).where(IshaaraRole.game_id == game_id))

    async def reseat_role(
        self, session: AsyncSession, game_id: uuid.UUID, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Point a seat at another registry player (merge)."""
        await session.execute(
            update(IshaaraRole)
            .where(IshaaraRole.game_id == game_id, IshaaraRole.player_id == from_id)
            .values(player_id=into_id)
        )

    async def action_seen(
        self, session: AsyncSession, game_id: uuid.UUID, action_id: uuid.UUID
    ) -> bool:
        """Whether this action id was already applied to the game."""
        stmt = select(
            exists().where(IshaaraAction.game_id == game_id, IshaaraAction.action_id == action_id)
        )
        return bool((await session.execute(stmt)).scalar())

    async def add_action(
        self,
        session: AsyncSession,
        *,
        game_id: uuid.UUID,
        action_id: uuid.UUID,
        body: dict[str, Any],
        at: datetime,
        by_user_id: uuid.UUID,
    ) -> None:
        """Log an applied action with the next seq (the caller holds the row lock)."""
        seq = (
            select(func.coalesce(func.max(IshaaraAction.seq), 0) + 1)
            .where(IshaaraAction.game_id == game_id)
            .scalar_subquery()
        )
        await session.execute(
            insert(IshaaraAction).values(
                game_id=game_id,
                action_id=action_id,
                seq=seq,
                type=str(body["type"]),
                body=body,
                at=at,
                by_user_id=by_user_id,
            )
        )

    async def list_page(
        self,
        session: AsyncSession,
        scope: ListScope,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[IshaaraListRow]:
        """Live games newest change first; reads `summary`, never `payload`."""
        stmt = (
            select(
                Game.id,
                Game.summary,
                func.coalesce(_host_player.display_name, ""),
                Game.updated_at,
            )
            .join(_host, _host.id == Game.owner_id)
            .outerjoin(_host_player, _host_player.id == _host.player_id)
            .where(Game.kind == KIND, Game.deleted_at.is_(None), *_scope_filters(scope))
            .order_by(Game.updated_at.desc(), Game.id.desc())
            .limit(limit)
        )
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(Game.updated_at < at, and_(Game.updated_at == at, Game.id < last_id))
            )
        return [IshaaraListRow(r[0], r[1], r[2], r[3]) for r in await session.execute(stmt)]

    async def accounts_of(
        self, session: AsyncSession, player_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID]:
        """player → the account linked to it, for the players that have one (one query)."""
        if not player_ids:
            return {}
        stmt = select(User.player_id, User.id).where(User.player_id.in_(player_ids))
        return {r[0]: r[1] for r in await session.execute(stmt) if r[0] is not None}

    async def host_name(self, session: AsyncSession, owner_id: uuid.UUID) -> str:
        """The host's display name (empty when the account has no player)."""
        stmt = (
            select(func.coalesce(Player.display_name, ""))
            .select_from(User)
            .outerjoin(Player, Player.id == User.player_id)
            .where(User.id == owner_id)
        )
        return str((await session.execute(stmt)).scalar_one())

    async def idle_ids(
        self, session: AsyncSession, idle_before: datetime, limit: int
    ) -> list[uuid.UUID]:
        """Unfinished live games unchanged since `idle_before`, oldest first."""
        stmt = (
            select(Game.id)
            .where(
                Game.kind == KIND,
                Game.status == "active",
                Game.deleted_at.is_(None),
                Game.updated_at < idle_before,
            )
            .order_by(Game.updated_at)
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())


ishaara_repository = IshaaraRepository()
