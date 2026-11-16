"""Spades online tables: the locked game row, private hands, seats, actions, codes and lists."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Game,
    Player,
    PlayerPhoto,
    SeatInvite,
    SpadesAction,
    SpadesHand,
    SpadesTableSeat,
    User,
)
from app.domain.enums import GameKind

KIND = GameKind.SPADES_ONLINE
_CODE = Game.summary["code"].astext


@dataclass(frozen=True)
class Occupant:
    """Who sits in a seat now."""

    seat: int
    player_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class PlayerCard:
    """An account's player, as a seat or an invited chair shows it."""

    user_id: uuid.UUID
    player_id: uuid.UUID
    name: str
    emoji: str
    color: str
    photo_rev: int
    has_photo: bool


@dataclass(frozen=True)
class PendingInvite:
    """A pending seat invite to a table, with the invitee's player."""

    id: uuid.UUID
    seat: str
    created_at: datetime
    card: PlayerCard


@dataclass(frozen=True)
class TableListRow:
    """What a table summary is built from."""

    game_id: uuid.UUID
    summary: dict[str, Any]
    updated_at: datetime
    owner_id: uuid.UUID


_CARD_COLUMNS = (
    User.id,
    Player.id,
    Player.display_name,
    Player.emoji,
    Player.color,
    Player.photo_rev,
    exists().where(PlayerPhoto.player_id == Player.id),
)


def _card(row: Sequence[Any]) -> PlayerCard:
    return PlayerCard(row[0], row[1], row[2] or "", row[3] or "", row[4] or "", row[5] or 0, row[6])


class SpadesTablesRepository:
    """Data access for online Spades tables."""

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
        """A live online table, unlocked."""
        row = await session.get(Game, game_id, populate_existing=True)
        if row is None or row.kind != KIND or row.deleted_at is not None:
            return None
        return row

    async def payload(self, session: AsyncSession, game_id: uuid.UUID) -> dict[str, Any] | None:
        """A live table's public state (the hub's read per change)."""
        stmt = select(Game.payload).where(
            Game.id == game_id, Game.kind == KIND, Game.deleted_at.is_(None)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def insert_game(self, session: AsyncSession, row: Game) -> None:
        """A new table row; IntegrityError when its open code is taken."""
        session.add(row)
        await session.flush()

    async def save(self, session: AsyncSession, row: Game) -> None:
        """Flush a changed game row."""
        session.add(row)
        await session.flush()

    async def code_open(self, session: AsyncSession, code: str) -> uuid.UUID | None:
        """The open table using a code (`uq_games_spades_code`)."""
        stmt = select(Game.id).where(
            Game.kind == KIND,
            Game.status == "active",
            Game.deleted_at.is_(None),
            code == _CODE,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def hands(self, session: AsyncSession, game_id: uuid.UUID) -> list[list[Any]] | None:
        """The four private hands by seat, or None before the first deal."""
        stmt = (
            select(SpadesHand.seat, SpadesHand.cards)
            .where(SpadesHand.game_id == game_id)
            .order_by(SpadesHand.seat)
        )
        rows = list(await session.execute(stmt))
        return [list(r[1]) for r in rows] if len(rows) == 4 else None

    async def save_hands(
        self, session: AsyncSession, game_id: uuid.UUID, hands: dict[int, list[Any]]
    ) -> None:
        """Write the hands of these seats (one statement)."""
        if not hands:
            return
        stmt = pg_insert(SpadesHand).values(
            [{"game_id": game_id, "seat": s, "cards": cards} for s, cards in hands.items()]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["game_id", "seat"], set_={"cards": stmt.excluded.cards}
            )
        )

    async def delete_hands(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Forget a table's private hands."""
        await session.execute(delete(SpadesHand).where(SpadesHand.game_id == game_id))

    async def sync_seats(
        self, session: AsyncSession, game_id: uuid.UUID, seated: list[Occupant], now: datetime
    ) -> None:
        """Seat rows to match the table: everyone off, then the occupants back on.

        A newcomer counts as seen when they sit; a replaced player keeps a row with no seat.
        """
        await session.execute(
            update(SpadesTableSeat)
            .where(SpadesTableSeat.game_id == game_id)
            .values(seat=None, user_id=None)
        )
        if not seated:
            return
        stmt = pg_insert(SpadesTableSeat).values(
            [
                {
                    "game_id": game_id,
                    "player_id": o.player_id,
                    "seat": o.seat,
                    "user_id": o.user_id,
                    "last_seen_at": now,
                }
                for o in seated
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["game_id", "player_id"],
                set_={"seat": stmt.excluded.seat, "user_id": stmt.excluded.user_id},
            )
        )

    async def mark_done(self, session: AsyncSession, game_id: uuid.UUID, at: datetime) -> None:
        """A hand ended: everyone seated played it."""
        await session.execute(
            update(SpadesTableSeat)
            .where(SpadesTableSeat.game_id == game_id, SpadesTableSeat.seat.is_not(None))
            .values(last_done_at=at)
        )

    async def touch_seen(
        self, session: AsyncSession, game_id: uuid.UUID, user_id: uuid.UUID, at: datetime
    ) -> None:
        """The account's seat at this table was seen now (stream or action)."""
        await session.execute(
            update(SpadesTableSeat)
            .where(
                SpadesTableSeat.game_id == game_id,
                SpadesTableSeat.user_id == user_id,
                SpadesTableSeat.seat.is_not(None),
            )
            .values(last_seen_at=at)
        )

    async def last_seen(
        self, session: AsyncSession, game_id: uuid.UUID, seat: int
    ) -> datetime | None:
        """When a seat was last seen."""
        stmt = select(SpadesTableSeat.last_seen_at).where(
            SpadesTableSeat.game_id == game_id, SpadesTableSeat.seat == seat
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def delete_seats(self, session: AsyncSession, game_id: uuid.UUID) -> list[uuid.UUID]:
        """Remove a table's seat rows; returns the players who had one."""
        stmt = (
            delete(SpadesTableSeat)
            .where(SpadesTableSeat.game_id == game_id)
            .returning(SpadesTableSeat.player_id)
        )
        return list((await session.execute(stmt)).scalars())

    async def reseat(
        self, session: AsyncSession, game_id: uuid.UUID, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Point a seat row at another registry player (merge)."""
        await session.execute(
            update(SpadesTableSeat)
            .where(SpadesTableSeat.game_id == game_id, SpadesTableSeat.player_id == from_id)
            .values(player_id=into_id)
        )

    async def card(self, session: AsyncSession, user_id: uuid.UUID) -> PlayerCard | None:
        """An account's player, or None when it has none."""
        stmt = (
            select(*_CARD_COLUMNS)
            .select_from(User)
            .join(Player, Player.id == User.player_id)
            .where(User.id == user_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        return None if row is None else _card(row)

    async def pending_invites(
        self, session: AsyncSession, game_id: uuid.UUID, since: datetime
    ) -> list[PendingInvite]:
        """A table's pending seat invites made since a time, with each invitee's player."""
        stmt = (
            select(SeatInvite.id, SeatInvite.seat, SeatInvite.created_at, *_CARD_COLUMNS)
            .join(User, User.id == SeatInvite.to_user)
            .join(Player, Player.id == User.player_id)
            .where(
                SeatInvite.game_id == game_id,
                SeatInvite.status == "pending",
                SeatInvite.created_at >= since,
            )
        )
        return [PendingInvite(r[0], r[1], r[2], _card(r[3:])) for r in await session.execute(stmt)]

    async def action_seen(
        self, session: AsyncSession, game_id: uuid.UUID, action_id: uuid.UUID
    ) -> bool:
        """Whether this action id was already applied to the table."""
        stmt = select(
            exists().where(SpadesAction.game_id == game_id, SpadesAction.action_id == action_id)
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
            select(func.coalesce(func.max(SpadesAction.seq), 0) + 1)
            .where(SpadesAction.game_id == game_id)
            .scalar_subquery()
        )
        await session.execute(
            insert(SpadesAction).values(
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
        player_id: uuid.UUID,
        status: str | None,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[TableListRow]:
        """Live tables a player sits (or sat) at, newest change first; never reads `payload`."""
        seated = exists().where(
            SpadesTableSeat.game_id == Game.id, SpadesTableSeat.player_id == player_id
        )
        stmt = (
            select(Game.id, Game.summary, Game.updated_at, Game.owner_id)
            .where(Game.kind == KIND, Game.deleted_at.is_(None), seated)
            .order_by(Game.updated_at.desc(), Game.id.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(Game.status == status)
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(Game.updated_at < at, and_(Game.updated_at == at, Game.id < last_id))
            )
        return [TableListRow(r[0], r[1], r[2], r[3]) for r in await session.execute(stmt)]

    async def idle_ids(
        self, session: AsyncSession, idle_before: datetime, limit: int
    ) -> list[uuid.UUID]:
        """Open tables unchanged since `idle_before`, oldest first."""
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


spades_tables_repository = SpadesTablesRepository()
