"""Ishaara's seat invites: a notification to each clue-giver account at create (plan D15).

Seats are `clue:<team>`; joining stays the key route, and a decline changes nothing in the game.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeatInvite, User
from app.exceptions import ApiError, ErrorCode
from app.repositories.ishaara import CLUE_GIVER, ishaara_repository
from app.repositories.seat_invites import InviteGame
from app.services.seat_invite_kinds import Staleness, TableView

SEAT_PREFIX = "clue:"


def clue_seat(team: str) -> str:
    """The seat text for a team's clue-giver."""
    return f"{SEAT_PREFIX}{team}"


def _over(game: InviteGame) -> bool:
    return game.deleted_at is not None or game.status == "finished"


class IshaaraSeatInvites:
    """The handler for kind `ishaara`."""

    async def can_invite(
        self, session: AsyncSession, game: InviteGame, to_user: User, seat: str
    ) -> None:
        """Only the clue-giver account fixed at create, for its own team, until it has joined."""
        if _over(game):
            raise ApiError(404, ErrorCode.NOT_FOUND, "Game not found.")
        roles = await ishaara_repository.roles(session, game.id)
        team = next(
            (r.team for r in roles if r.role == CLUE_GIVER and clue_seat(r.team) == seat), None
        )
        holder = next((r.user_id for r in roles if r.role == CLUE_GIVER and r.team == team), None)
        if team is None or holder != to_user.id:
            raise ApiError(409, ErrorCode.SEAT_TAKEN, "That seat is someone else's.")
        payload = await ishaara_repository.payload(session, game.id)
        if payload is not None and payload["joined"].get(team):
            raise ApiError(409, ErrorCode.ALREADY_SEATED, "They have already joined.")

    def stale(self, game: InviteGame, invite: SeatInvite) -> Staleness | None:
        """Out of date once the game is over or deleted."""
        return Staleness.EXPIRED if _over(game) else None

    async def on_accept(self, session: AsyncSession, invite: SeatInvite, user: User) -> None:
        """Nothing to seat: the app opens the key route, which joins the team."""

    def table(self, game: InviteGame, invite: SeatInvite, host_name: str) -> TableView:
        """The host's game; no seat map, no partner."""
        return TableView(name=f"{host_name}'s game")

    async def after_change(self, session: AsyncSession, invite: SeatInvite) -> None:
        """No table to refresh: the game's own stream carries joins."""
