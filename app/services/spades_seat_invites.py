"""Spades' seat invites: the host offers an open chair, in the lobby or after a Replace.

Seats are "0"-"3". Accepting sits the invitee through the domain's `sit` (as the join route
does); every change refreshes the table's followers, whose lobby shows the invited chair.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeatInvite, User
from app.domain.spades import sit
from app.exceptions import ApiError, ErrorCode
from app.repositories.seat_invites import InviteGame
from app.services.seat_invite_kinds import Staleness, TableView
from app.services.spades_store import SpadesStore, now_ms

SEATS = ("0", "1", "2", "3")


def _over(game: InviteGame) -> bool:
    status = game.summary.get("status")
    return game.deleted_at is not None or status not in ("lobby", "playing")


def _seat_open(summary: dict[str, Any], seat: int) -> bool:
    """The chair is empty and may be filled now: the lobby, or the seat a Replace opened."""
    if summary["seats"][seat] is not None:
        return False
    return summary["status"] == "lobby" or summary.get("paused") == "replace"


def _seated(summary: dict[str, Any], user: User) -> bool:
    mine = str(user.player_id)
    return any(s is not None and s.get("playerId") == mine for s in summary["seats"])


class SpadesSeatInvites:
    """The handler for kind `spades-online`."""

    async def can_invite(
        self, session: AsyncSession, game: InviteGame, to_user: User, seat: str
    ) -> None:
        """An open chair only, to someone not at the table."""
        if _over(game):
            raise ApiError(404, ErrorCode.NOT_FOUND, "Game not found.")
        if seat not in SEATS:
            raise ApiError(422, ErrorCode.INVALID, "Bad seat.", fields={"seat": "0 to 3"})
        if _seated(game.summary, to_user):
            raise ApiError(409, ErrorCode.ALREADY_SEATED, "They are already at the table.")
        if not _seat_open(game.summary, int(seat)):
            raise ApiError(409, ErrorCode.SEAT_TAKEN, "That seat isn't open.")

    def stale(self, game: InviteGame, invite: SeatInvite) -> Staleness | None:
        """Expired once the table is over; filled once the chair is taken or play has moved on."""
        if _over(game):
            return Staleness.EXPIRED
        if invite.seat not in SEATS or not _seat_open(game.summary, int(invite.seat)):
            return Staleness.FILLED
        return None

    async def on_accept(self, session: AsyncSession, invite: SeatInvite, user: User) -> None:
        """Sit the invitee in the invited chair; 409 `filled` if the table can't take them."""
        store = SpadesStore(session)
        row = await store.locked(invite.game_id)
        who = await store.who(user)
        result = sit(row.payload, who, int(invite.seat), now_ms())
        if not result.ok:
            raise ApiError(409, ErrorCode.FILLED, "This table has filled up.")
        if result.state is not row.payload:
            await store.save(row, result.state)

    def table(self, game: InviteGame, invite: SeatInvite, host_name: str) -> TableView:
        """The table's name, its taken seats, and who sits opposite the invited chair."""
        seats = game.summary["seats"]
        taken = tuple(i for i, s in enumerate(seats) if s is not None)
        partner = None
        if invite.seat in SEATS:
            mate = seats[(int(invite.seat) + 2) % 4]
            partner = None if mate is None else mate["name"]
        return TableView(name=game.summary["name"], seats_taken=taken, partner=partner)

    async def after_change(self, session: AsyncSession, invite: SeatInvite) -> None:
        """The lobby shows invited chairs: refresh the table's followers."""
        await SpadesStore(session).tell(invite.game_id)
