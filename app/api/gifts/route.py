"""Gifts (rewards finish §5): send an item to a circle member, the table's pending gifts, opening
one, and my gifts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentAuth, get_gifts_service
from app.schemas.common import Page
from app.schemas.wardrobe import GiftIn, GiftListOut, GiftOut
from app.services.gifts import GiftsService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

_GiftsServiceDep = Annotated[GiftsService, Depends(get_gifts_service)]

gifts_router = APIRouter(prefix="/gifts", tags=["Gifts"])
me_gifts_router = APIRouter(prefix="/me/gifts", tags=["Gifts"])


@gifts_router.post("", response_model=GiftOut)
async def send_gift(body: GiftIn, ctx: CurrentAuth, service: _GiftsServiceDep) -> GiftOut:
    """Send an item from my balance; the same `giftId` returns the first gift. Errors:
    422 `not_in_circle`, 422 `self_gift`, 409 `already_owned`, 422 `not_enough_sikke
    {balance, price}`, 422 `not_for_sale`, 409 `id_conflict`, 409 `no_player`."""
    return await service.send(ctx.user, body)


@gifts_router.get("/pending", response_model=GiftListOut)
async def list_pending_gifts(
    players: Annotated[str, Query(max_length=600, description="Up to 12 player ids, commas")],
    _: CurrentAuth,
    service: _GiftsServiceDep,
) -> GiftListOut:
    """Unopened gifts to any of these players, oldest first (the table device, as a game starts)."""
    return await service.pending(players)


@gifts_router.post("/{gift_id}/open", response_model=GiftOut)
async def open_gift(
    gift_id: Annotated[uuid.UUID, Path(description="Gift id")],
    ctx: CurrentAuth,
    service: _GiftsServiceDep,
) -> GiftOut:
    """Mark a gift shown (idempotent): the receiver's account or one whose circle has them.
    404 `not_found` for anyone else."""
    return await service.open(gift_id, ctx.user)


@me_gifts_router.get("", response_model=Page[GiftOut])
async def list_my_gifts(
    ctx: CurrentAuth,
    service: _GiftsServiceDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[GiftOut]:
    """My player's sent and received gifts, newest first. 409 `no_player` without one."""
    return await service.mine(ctx.user, cursor=cursor, limit=limit)
