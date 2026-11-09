"""Seat claims."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SeatClaim
from app.domain.enums import GameKind


class ClaimsRepository:
    """Data access for `seat_claims`."""

    async def get(self, session: AsyncSession, claim_id: uuid.UUID) -> SeatClaim | None:
        """A claim by id."""
        return await session.get(SeatClaim, claim_id)

    async def pending_for(
        self, session: AsyncSession, player_id: uuid.UUID, claimant_id: uuid.UUID
    ) -> SeatClaim | None:
        """The pending claim of one person on one player."""
        stmt = select(SeatClaim).where(
            SeatClaim.player_id == player_id,
            SeatClaim.claimant_user_id == claimant_id,
            SeatClaim.status == "pending",
        )
        return (await session.execute(stmt)).scalars().first()

    async def create(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        player_id: uuid.UUID,
        claimant_id: uuid.UUID,
    ) -> SeatClaim:
        """Insert a pending claim."""
        claim = SeatClaim(game_id=game_id, player_id=player_id, claimant_user_id=claimant_id)
        session.add(claim)
        await session.flush()
        await session.refresh(claim)
        return claim

    async def decidable(
        self, session: AsyncSession, user_id: uuid.UUID, everything: bool
    ) -> list[SeatClaim]:
        """Pending claims on live Kachuful games the user scored (all for an admin), newest first."""
        stmt = (
            select(SeatClaim)
            .join(Game, Game.id == SeatClaim.game_id)
            .where(
                SeatClaim.status == "pending",
                Game.kind == GameKind.KACHUFUL,
                Game.deleted_at.is_(None),
            )
            .order_by(SeatClaim.created_at.desc())
        )
        if not everything:
            stmt = stmt.where(or_(Game.owner_id == user_id))
        return list((await session.execute(stmt)).scalars())


claims_repository = ClaimsRepository()
