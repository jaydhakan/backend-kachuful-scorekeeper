"""Seat claims: "that seat is me", approved by the scorer or the admin, then a merge."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeatClaim, User
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.claims import claims_repository
from app.repositories.games import games_repository
from app.repositories.players import players_repository
from app.repositories.users import users_repository
from app.schemas.claims import ClaimantOut, ClaimOut, ClaimPlayerOut, ClaimsOut
from app.services.merge import MergeService

logger = get_logger(__name__)


def _invalid(reason: str) -> ApiError:
    return ApiError(
        422, ErrorCode.INVALID, "That seat can't be claimed.", fields={"playerId": reason}
    )


class ClaimsService:
    """`/api/games/{id}/claims` and `/api/claims`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _out(self, claim: SeatClaim) -> ClaimOut:
        claimant = await users_repository.get(self.session, claim.claimant_user_id)
        ids = [claim.player_id] + ([claimant.player_id] if claimant and claimant.player_id else [])
        views = await players_repository.get_views(self.session, ids)
        seat = views.get(claim.player_id)
        theirs = views.get(claimant.player_id) if claimant and claimant.player_id else None
        return ClaimOut(
            id=claim.id,
            game_id=claim.game_id,
            status=claim.status,
            created_at=to_ms(claim.created_at),
            player=ClaimPlayerOut(
                id=claim.player_id, display_name=seat.player.display_name if seat else ""
            ),
            claimant=ClaimantOut(
                user_id=claim.claimant_user_id,
                player_id=claimant.player_id if claimant else None,
                display_name=theirs.player.display_name if theirs else "",
            ),
        )

    async def create(self, game_id: uuid.UUID, player_id: uuid.UUID, user: User) -> ClaimOut:
        """Ask to take over a seat's player; the scorer or the admin decides."""
        game = await games_repository.get(self.session, game_id)
        if game is None or game.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        seated = {seat.get("playerId") for seat in game.payload.get("players", [])}
        if str(player_id) not in seated:
            raise _invalid("not at that table")
        if user.player_id is None:
            raise _invalid("you have no player")
        if str(user.player_id) in seated:
            raise _invalid("you are already at that table")
        view = await players_repository.get_view(self.session, player_id)
        if view is None or view.player.merged_into is not None:
            raise _invalid("no such player")
        if view.linked_user_id is not None:
            raise ApiError(409, ErrorCode.PLAYER_LINKED, "That player already has an account.")
        if await claims_repository.pending_for(self.session, player_id, user.id) is not None:
            raise ApiError(409, ErrorCode.CLAIM_PENDING, "You already asked for this seat.")
        claim = await claims_repository.create(self.session, game_id, player_id, user.id)
        await self.session.commit()
        logger.info("seat claimed", extra={"game_id": str(game_id)})
        return await self._out(claim)

    async def decidable(self, user: User) -> ClaimsOut:
        """Pending claims the caller can decide."""
        claims = await claims_repository.decidable(self.session, user.id, user.role == "admin")
        return ClaimsOut(items=[await self._out(c) for c in claims])

    async def _deciding(self, claim_id: uuid.UUID, user: User) -> SeatClaim:
        claim = await claims_repository.get(self.session, claim_id)
        if claim is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such claim.")
        game = await games_repository.get(self.session, claim.game_id)
        if game is None or game.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        if game.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin decides.")
        if claim.status != "pending":
            raise ApiError(422, ErrorCode.INVALID, "That claim was already decided.")
        return claim

    async def approve(self, claim_id: uuid.UUID, user: User) -> ClaimOut:
        """Merge the claimed player into the claimant's; their history comes along."""
        claim = await self._deciding(claim_id, user)
        claimant = await users_repository.get(self.session, claim.claimant_user_id)
        if claimant is None or claimant.player_id is None:
            raise _invalid("the claimant has no player")
        claim_key, player_id, into = claim.id, claim.player_id, claimant.player_id
        await MergeService(self.session).merge(player_id, into, user)
        decided = await claims_repository.get(self.session, claim_key)
        assert decided is not None
        decided.status, decided.decided_by, decided.decided_at = "approved", user.id, now_utc()
        await self.session.commit()
        logger.info("seat claim approved", extra={"claim_id": str(claim_key)})
        return await self._out(decided)

    async def reject(self, claim_id: uuid.UUID, user: User) -> ClaimOut:
        """Turn the claim down; nothing moves."""
        claim = await self._deciding(claim_id, user)
        claim.status, claim.decided_by, claim.decided_at = "rejected", user.id, now_utc()
        await self.session.commit()
        return await self._out(claim)
