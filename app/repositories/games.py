"""Stored games of every kind; reads take a kind. Lists read `summary`, never `payload`."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnClause,
    ColumnElement,
    and_,
    func,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Game, Player, ShareLink, User
from app.domain.enums import GameKind
from app.repositories.seats import seat_rows


@dataclass(frozen=True)
class SummaryRow:
    """What a GameSummary is built from: the stored summary plus owner and live state."""

    game_id: uuid.UUID
    summary: dict[str, Any]
    owner_id: uuid.UUID
    owner_name: str
    updated_at: datetime
    deleted_at: datetime | None
    live: bool
    photos: dict[str, int]


_owner = aliased(User)
_owner_player = aliased(Player)


def live_link_exists() -> ColumnElement[bool]:
    """Whether a game has an active live link."""
    return (
        select(ShareLink.token)
        .where(
            ShareLink.game_id == Game.id,
            ShareLink.kind == "live",
            ShareLink.revoked_at.is_(None),
        )
        .exists()
    )


# playerId → photo_rev for the seated players that have a photo (photos change after upload).
_PHOTOS: ColumnClause[Any] = literal_column(
    "(SELECT coalesce(jsonb_object_agg(gp.player_id::text, p.photo_rev), '{}'::jsonb)"
    " FROM game_players gp JOIN players p ON p.id = gp.player_id"
    " JOIN player_photos ph ON ph.player_id = p.id WHERE gp.game_id = games.id)"
)


def _summary_columns() -> tuple[Any, ...]:
    return (
        Game.id,
        Game.summary,
        Game.owner_id,
        func.coalesce(_owner_player.display_name, ""),
        Game.updated_at,
        Game.deleted_at,
        live_link_exists(),
        _PHOTOS,
    )


def _with_owner(stmt: Any) -> Any:
    return stmt.join(_owner, _owner.id == Game.owner_id).outerjoin(
        _owner_player, _owner_player.id == _owner.player_id
    )


def _rows(result: Any) -> list[SummaryRow]:
    return [
        SummaryRow(
            game_id=r[0],
            summary=r[1],
            owner_id=r[2],
            owner_name=r[3],
            updated_at=r[4],
            deleted_at=r[5],
            live=bool(r[6]),
            photos=dict(r[7] or {}),
        )
        for r in result
    ]


class GamesRepository:
    """Data access for `games`."""

    async def lock(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Serialise writes to one game for the rest of the transaction."""
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"), {"id": str(game_id)}
        )

    async def get(
        self, session: AsyncSession, game_id: uuid.UUID, *, kind: GameKind | None
    ) -> Game | None:
        """A game row (with payload) of this kind; `kind=None` returns a row of any kind."""
        row = await session.get(Game, game_id, populate_existing=True)
        if row is None or (kind is not None and row.kind != kind):
            return None
        return row

    async def save(self, session: AsyncSession, game: Game) -> None:
        """Insert or update a game row."""
        session.add(game)
        await session.flush()

    async def summaries(
        self, session: AsyncSession, game_ids: list[uuid.UUID], *, kind: GameKind
    ) -> dict[uuid.UUID, SummaryRow]:
        """Summaries for some games of this kind, in one query."""
        if not game_ids:
            return {}
        stmt = _with_owner(select(*_summary_columns())).where(
            Game.id.in_(set(game_ids)), Game.kind == kind
        )
        return {row.game_id: row for row in _rows((await session.execute(stmt)).all())}

    async def list_page(
        self,
        session: AsyncSession,
        *,
        kind: GameKind,
        updated_since: datetime | None,
        before: tuple[datetime, uuid.UUID] | None,
        owner_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        involving: tuple[uuid.UUID, uuid.UUID | None] | None = None,
    ) -> list[SummaryRow]:
        """Newest change first. With `updated_since`, deleted games come back as tombstones.

        `involving` = (user, their player): games the user scored or sat in, matched on the stored
        payload so deleted games (whose derived rows are gone) still come back as tombstones.
        """
        stmt = (
            _with_owner(select(*_summary_columns()))
            .where(Game.kind == kind)
            .order_by(Game.updated_at.desc(), Game.id.desc())
        )
        if updated_since is None:
            stmt = stmt.where(Game.deleted_at.is_(None))
        else:
            stmt = stmt.where(Game.updated_at > updated_since)
        if involving is not None:
            user_id, player_id = involving
            seated = Game.payload["players"].contains([{"playerId": str(player_id)}])
            stmt = stmt.where(
                Game.owner_id == user_id
                if player_id is None
                else or_(Game.owner_id == user_id, seated)
            )
        elif owner_id is not None:
            stmt = stmt.where(Game.owner_id == owner_id)
        if active_only:
            stmt = stmt.where(
                Game.status == "active", Game.ended_at.is_(None), Game.deleted_at.is_(None)
            )
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(Game.updated_at < at, and_(Game.updated_at == at, Game.id < last_id))
            )
        return _rows((await session.execute(stmt.limit(limit))).all())

    async def owned_summaries(
        self, session: AsyncSession, owner_id: uuid.UUID, *, kind: GameKind
    ) -> list[SummaryRow]:
        """Every live game of this kind a user uploaded, newest first (bootstrap)."""
        stmt = (
            _with_owner(select(*_summary_columns()))
            .where(Game.owner_id == owner_id, Game.kind == kind, Game.deleted_at.is_(None))
            .order_by(Game.updated_at.desc(), Game.id.desc())
        )
        return _rows((await session.execute(stmt)).all())

    async def owned_ids(
        self, session: AsyncSession, owner_id: uuid.UUID, *, kind: GameKind
    ) -> list[uuid.UUID]:
        """Ids of every live game of this kind a user uploaded."""
        stmt = select(Game.id).where(
            Game.owner_id == owner_id, Game.kind == kind, Game.deleted_at.is_(None)
        )
        return list((await session.execute(stmt.order_by(Game.id))).scalars())

    async def soft_delete(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Mark deleted; `updated_at` moves so sync clients see the tombstone."""
        await session.execute(
            update(Game)
            .where(Game.id == game_id)
            .values(deleted_at=func.now(), updated_at=func.now())
        )

    async def counts(self, session: AsyncSession, *, kind: GameKind) -> tuple[int, int]:
        """(live games of this kind, done rounds across them)."""
        stmt = select(func.count(), func.coalesce(func.sum(Game.round_count), 0)).where(
            Game.kind == kind, Game.deleted_at.is_(None)
        )
        games, rounds = (await session.execute(stmt)).one()
        return int(games), int(rounds)

    async def auto_end_candidates(
        self, session: AsyncSession, idle_before: datetime, limit: int, *, kind: GameKind
    ) -> list[uuid.UUID]:
        """Active games of this kind untouched since `idle_before` with a round done (plan §9)."""
        stmt = (
            select(Game.id)
            .where(
                Game.kind == kind,
                Game.status == "active",
                Game.ended_at.is_(None),
                Game.deleted_at.is_(None),
                Game.client_updated_at < idle_before,
                Game.round_count > 0,
            )
            .order_by(Game.client_updated_at)
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())

    async def empty_ids(
        self, session: AsyncSession, *, kind: GameKind
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """(game, owner) of every live game of this kind with no round done.

        Every kind keeps `round_count` (its rounds, hands or words), so this works for each one.
        """
        stmt = select(Game.id, Game.owner_id).where(
            Game.kind == kind, Game.deleted_at.is_(None), Game.round_count == 0
        )
        return [(r[0], r[1]) for r in (await session.execute(stmt.order_by(Game.id))).all()]

    async def ids_with_player(self, session: AsyncSession, player_id: uuid.UUID) -> list[uuid.UUID]:
        """Live games of any kind a registry player is seated in."""
        seats = seat_rows()
        stmt = (
            select(seats.c.game_id)
            .join(Game, Game.id == seats.c.game_id)
            .where(seats.c.player_id == player_id, Game.deleted_at.is_(None))
            .order_by(seats.c.game_id)
        )
        return list((await session.execute(stmt)).scalars())


games_repository = GamesRepository()
