"""Seat claims (contract change 2026-11-06)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import CurrentAuth, get_claims_service
from app.schemas.claims import ClaimIn, ClaimOut, ClaimsOut
from app.services.claims import ClaimsService

_ClaimsServiceDep = Annotated[ClaimsService, Depends(get_claims_service)]
_ClaimId = Annotated[uuid.UUID, Path(description="Claim id")]

claims_router = APIRouter(tags=["Claims"])


@claims_router.post("/games/{game_id}/claims", response_model=ClaimOut, status_code=201)
async def claim_seat(
    game_id: Annotated[uuid.UUID, Path(description="The game's sync id")],
    body: ClaimIn,
    ctx: CurrentAuth,
    service: _ClaimsServiceDep,
) -> ClaimOut:
    """Say a seat's player is you; the scorer or the admin approves."""
    return await service.create(game_id, body.player_id, ctx.user)


@claims_router.get("/claims", response_model=ClaimsOut)
async def list_claims(ctx: CurrentAuth, service: _ClaimsServiceDep) -> ClaimsOut:
    """Pending claims on games you scored (every game for the admin)."""
    return await service.decidable(ctx.user)


@claims_router.post("/claims/{claim_id}/approve", response_model=ClaimOut)
async def approve_claim(
    claim_id: _ClaimId, ctx: CurrentAuth, service: _ClaimsServiceDep
) -> ClaimOut:
    """Approve: the claimed player merges into the claimant's."""
    return await service.approve(claim_id, ctx.user)


@claims_router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
async def reject_claim(
    claim_id: _ClaimId, ctx: CurrentAuth, service: _ClaimsServiceDep
) -> ClaimOut:
    """Turn a claim down."""
    return await service.reject(claim_id, ctx.user)
