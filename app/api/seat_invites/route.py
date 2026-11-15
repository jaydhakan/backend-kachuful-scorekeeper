"""Seat invites and the circle they go to.

Each game kind creates its own invites (`POST /api/<game>/.../seat-invites`); these routes read,
answer and cancel them for every kind.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import CurrentAuth, get_seat_invites_service
from app.schemas.seat_invites import CircleOut, SeatInviteOut, SeatInvitesOut
from app.services.seat_invites import SeatInvitesService

_InvitesDep = Annotated[SeatInvitesService, Depends(get_seat_invites_service)]
_InviteId = Annotated[uuid.UUID, Path(description="Seat invite id")]

seat_invites_router = APIRouter(prefix="/seat-invites", tags=["Seat invites"])
me_circle_router = APIRouter(prefix="/me", tags=["Seat invites"])


@me_circle_router.get("/circle", response_model=CircleOut)
async def get_my_circle(ctx: CurrentAuth, service: _InvitesDep) -> CircleOut:
    """People I can invite to a seat: in my circle, with an account, never me. Last played first.

    "Online" is `serverNow - lastSeenAt < 60000`, decided by the app.
    """
    return await service.circle(ctx.user)


@seat_invites_router.get("", response_model=SeatInvitesOut)
async def list_my_seat_invites(ctx: CurrentAuth, service: _InvitesDep) -> SeatInvitesOut:
    """My pending seat invites, newest first; expired ones (worked out on read) are left out."""
    return await service.mine(ctx.user)


@seat_invites_router.post("/{invite_id}/accept", response_model=SeatInviteOut)
async def accept_seat_invite(
    invite_id: _InviteId, ctx: CurrentAuth, service: _InvitesDep
) -> SeatInviteOut:
    """Take the seat (Spades) or mark the invite accepted (Ishaara: join through the key route).

    `410 expired` when out of date or answered; `409 filled` when the seat or game moved on.
    """
    return await service.accept(invite_id, ctx.user)


@seat_invites_router.post("/{invite_id}/decline", status_code=204)
async def decline_seat_invite(invite_id: _InviteId, ctx: CurrentAuth, service: _InvitesDep) -> None:
    """Say no (the invitee). An invite already answered stays as it is."""
    await service.decline(invite_id, ctx.user)


@seat_invites_router.delete("/{invite_id}", status_code=204)
async def cancel_seat_invite(invite_id: _InviteId, ctx: CurrentAuth, service: _InvitesDep) -> None:
    """Take an invite back (the host; `403 not_host` for the invitee)."""
    await service.cancel(invite_id, ctx.user)
