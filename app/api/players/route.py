"""Player registry routes (plan §6.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, get_players_service
from app.schemas.common import Page
from app.schemas.players import NameCheckOut, NewPlayerIn, PlayerOut, PlayerPatchIn
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.players import PlayersService
from app.services.presenters import player_out

_PlayersServiceDep = Annotated[PlayersService, Depends(get_players_service)]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]

players_router = APIRouter(prefix="/players", tags=["Players"])


@players_router.get("", response_model=Page[PlayerOut])
async def list_players(
    _: CurrentAuth,
    service: _PlayersServiceDep,
    updated_since: Annotated[int | None, Query(alias="updatedSince", ge=0)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[PlayerOut]:
    """The whole registry in sync order (oldest change first), merged players included."""
    return await service.list_page(updated_since=updated_since, cursor=cursor, limit=limit)


@players_router.get("/check", response_model=NameCheckOut)
async def check_name(
    _: CurrentAuth,
    service: _PlayersServiceDep,
    name: Annotated[str, Query(min_length=1, max_length=100)],
) -> NameCheckOut:
    """Whether a display name is free (case and extra spaces ignored)."""
    return await service.check(name)


@players_router.post("", response_model=PlayerOut, status_code=201)
async def create_player(
    body: NewPlayerIn, ctx: CurrentAuth, service: _PlayersServiceDep, response: Response
) -> PlayerOut:
    """Add a player. The same id and body again answers 200 with the stored player."""
    result = await service.create_and_commit(body, ctx.user)
    if not result.created:
        response.status_code = 200
    return player_out(result.view)


@players_router.patch("/{player_id}", response_model=PlayerOut)
async def update_player(
    player_id: _PlayerId, body: PlayerPatchIn, ctx: CurrentAuth, service: _PlayersServiceDep
) -> PlayerOut:
    """Edit a player: name by admin or the linked user; the rest also by the creator."""
    return await service.patch(player_id, body, ctx.user)
