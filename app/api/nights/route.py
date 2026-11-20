"""Nights (rewards plan §7.2): a player's nights, one night's story, and closing one's own now."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentAuth, get_nights_service
from app.schemas.common import Page
from app.schemas.nights import NightDetailOut, NightItemOut
from app.services.nights import NightsService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

_NightsServiceDep = Annotated[NightsService, Depends(get_nights_service)]

nights_router = APIRouter(prefix="/nights", tags=["Nights"])


@nights_router.get("", response_model=Page[NightItemOut])
async def list_nights(
    ctx: CurrentAuth,
    service: _NightsServiceDep,
    player_id: Annotated[
        uuid.UUID | None, Query(alias="playerId", description="Default: my own player")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[NightItemOut]:
    """A player's nights, newest first. On the first page the open night (not closed yet,
    computed on read) comes first with `closed: false` and `id: null`."""
    return await service.page(player_id, ctx.user, cursor=cursor, limit=limit)


@nights_router.post("/close", response_model=NightDetailOut)
async def close_my_night(ctx: CurrentAuth, service: _NightsServiceDep) -> NightDetailOut:
    """End the night: close my open night now, as `close-nights` would. 404 `not_found` when I
    have none."""
    return await service.close_mine(ctx.user)


@nights_router.get("/{night_id}", response_model=NightDetailOut)
async def get_night(
    night_id: Annotated[uuid.UUID, Path(description="Night id")],
    _: CurrentAuth,
    service: _NightsServiceDep,
) -> NightDetailOut:
    """One night and its story (`data` null while it is open)."""
    return await service.detail(night_id)
