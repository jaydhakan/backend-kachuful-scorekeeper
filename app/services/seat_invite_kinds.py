"""What a game kind supplies for seat invites; each kind's handler is listed in
`services/seat_invites.SEAT_INVITE_KINDS` (a kind's `seat_invites.kind` equals its `games.kind`).

The generic service already checks, before `can_invite`: the game exists, isn't deleted and is of
this kind (404), the caller owns it (403 `not_host`), the invitee is an active account with a
player (409 `no_account`) in the host's circle and isn't the host (409 `not_in_circle`). It holds
the game row FOR UPDATE through a create, and works out the 30-minute expiry itself.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeatInvite, User
from app.repositories.seat_invites import InviteGame


class Staleness(StrEnum):
    """Why a pending invite can no longer be taken: read as `expired`; accept says 410 or 409."""

    EXPIRED = "expired"
    FILLED = "filled"


@dataclass(frozen=True)
class TableView:
    """The invite card's table: name, seats taken and who the invitee would partner."""

    name: str
    seats_taken: tuple[int, ...] = ()
    partner: str | None = None


class SeatInviteKind(Protocol):
    """One game kind's rules for seat invites."""

    async def can_invite(
        self, session: AsyncSession, game: InviteGame, to_user: User, seat: str
    ) -> None:
        """Raise when this seat can't be offered to `to_user` now (409 `seat_taken`,
        409 `already_seated`, or 404 once the game is over)."""
        ...

    def stale(self, game: InviteGame, invite: SeatInvite) -> Staleness | None:
        """Why a pending invite is out of date, from the game's columns and summary; None if live."""
        ...

    async def on_accept(self, session: AsyncSession, invite: SeatInvite, user: User) -> None:
        """Seat the invitee (no commit). Raise 409 `filled` before writing anything if it can't."""
        ...

    def table(self, game: InviteGame, invite: SeatInvite, host_name: str) -> TableView:
        """The table as the invite card shows it."""
        ...

    async def after_change(self, session: AsyncSession, invite: SeatInvite) -> None:
        """After any lifecycle change, before commit (Spades: NOTIFY the table's followers)."""
        ...
