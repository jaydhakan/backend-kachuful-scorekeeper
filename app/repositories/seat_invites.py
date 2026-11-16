"""Seat invites, read with their game's facts and the host's player in one query."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Player, PlayerPhoto, SeatInvite, User

PENDING = "pending"


@dataclass(frozen=True)
class InviteGame:
    """What invites need of a game: never its payload."""

    id: uuid.UUID
    kind: str
    owner_id: uuid.UUID
    status: str
    deleted_at: datetime | None
    summary: dict[str, Any]


@dataclass(frozen=True)
class InviteHost:
    """The inviting account and its player (name empty when the account has none)."""

    user_id: uuid.UUID
    player_id: uuid.UUID | None
    name: str
    emoji: str
    color: str
    photo_rev: int
    has_photo: bool


@dataclass(frozen=True)
class InviteView:
    """An invite with its game and host."""

    invite: SeatInvite
    game: InviteGame
    host: InviteHost


_GAME_COLUMNS = (Game.id, Game.kind, Game.owner_id, Game.status, Game.deleted_at, Game.summary)
_HOST_COLUMNS = (
    User.id,
    Player.id,
    Player.display_name,
    Player.emoji,
    Player.color,
    Player.photo_rev,
    exists().where(PlayerPhoto.player_id == Player.id),
)


def _host(row: Any) -> InviteHost:
    user_id, player_id, name, emoji, color, photo_rev, has_photo = row
    return InviteHost(
        user_id=user_id,
        player_id=player_id,
        name=name or "",
        emoji=emoji or "",
        color=color or "",
        photo_rev=photo_rev or 0,
        has_photo=bool(has_photo),
    )


def _views_stmt(*where: ColumnElement[bool]) -> Select[Any]:
    return (
        select(SeatInvite, *_GAME_COLUMNS, *_HOST_COLUMNS)
        .join(Game, Game.id == SeatInvite.game_id)
        .join(User, User.id == SeatInvite.from_user)
        .outerjoin(Player, Player.id == User.player_id)
        .where(*where)
    )


def _view(row: Any) -> InviteView:
    return InviteView(invite=row[0], game=InviteGame(*row[1:7]), host=_host(row[7:]))


class SeatInvitesRepository:
    """Data access for `seat_invites`."""

    async def pending_for(
        self, session: AsyncSession, to_user: uuid.UUID, since: datetime
    ) -> list[InviteView]:
        """A user's pending invites made since a time, newest first."""
        stmt = _views_stmt(
            SeatInvite.to_user == to_user,
            SeatInvite.status == PENDING,
            SeatInvite.created_at >= since,
        ).order_by(SeatInvite.created_at.desc(), SeatInvite.id.desc())
        return [_view(r) for r in await session.execute(stmt)]

    async def view(
        self, session: AsyncSession, invite_id: uuid.UUID, *, lock: bool = False
    ) -> InviteView | None:
        """One invite with its game and host; `lock` holds the invite row until commit."""
        stmt = _views_stmt(SeatInvite.id == invite_id).execution_options(populate_existing=True)
        if lock:
            stmt = stmt.with_for_update(of=SeatInvite)
        row = (await session.execute(stmt)).one_or_none()
        return None if row is None else _view(row)

    async def game(
        self, session: AsyncSession, game_id: uuid.UUID, *, lock: bool = False
    ) -> InviteGame | None:
        """A game's facts for invites; `lock` holds the game row until commit."""
        stmt = select(*_GAME_COLUMNS).where(Game.id == game_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await session.execute(stmt)).one_or_none()
        return None if row is None else InviteGame(*row)

    async def host(self, session: AsyncSession, user_id: uuid.UUID) -> InviteHost:
        """An account as a host."""
        stmt = (
            select(*_HOST_COLUMNS)
            .select_from(User)
            .outerjoin(Player, Player.id == User.player_id)
            .where(User.id == user_id)
        )
        return _host((await session.execute(stmt)).one())

    async def insert(self, session: AsyncSession, rows: list[dict[str, Any]]) -> None:
        """New invites (one executemany)."""
        if rows:
            await session.execute(insert(SeatInvite), rows)

    async def settle_pending(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        *,
        status: str,
        at: datetime,
        to_user: uuid.UUID | None = None,
        seat: str | None = None,
    ) -> list[SeatInvite]:
        """Give a game's pending invites (to this user or for this seat, when given) a final status."""
        where = [SeatInvite.game_id == game_id, SeatInvite.status == PENDING]
        matches = [SeatInvite.to_user == to_user] if to_user is not None else []
        matches += [SeatInvite.seat == seat] if seat is not None else []
        if matches:
            where.append(or_(*matches))
        stmt = (
            update(SeatInvite)
            .where(*where)
            .values(status=status, responded_at=at)
            .returning(SeatInvite)
            .execution_options(synchronize_session=False)
        )
        return list((await session.execute(stmt)).scalars())

    async def set_status(
        self, session: AsyncSession, invite: SeatInvite, status: str, at: datetime
    ) -> None:
        """Record an invite's final status."""
        invite.status = status
        invite.responded_at = at
        await session.flush()


seat_invites_repository = SeatInvitesRepository()
