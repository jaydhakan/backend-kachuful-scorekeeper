"""Game sync routes (plan §6.4)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query

from app.api.deps import CurrentAuth, get_games_service
from app.schemas.games import GameDetailOut, GamesPageOut, UploadOut
from app.security import rate_limit
from app.services.games import GamesService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

_GamesServiceDep = Annotated[GamesService, Depends(get_games_service)]
_GameId = Annotated[uuid.UUID, Path(description="The game's sync id")]

games_router = APIRouter(prefix="/games", tags=["Games"])


@games_router.put("/{game_id}", response_model=UploadOut, response_model_exclude_none=True)
async def upload_game(
    game_id: _GameId,
    payload: Annotated[dict[str, Any], Body(description="The app's Game (plan §5.2)")],
    ctx: CurrentAuth,
    service: _GamesServiceDep,
) -> UploadOut:
    """Store a game. Same payload again is a no-op; an older or equal rev with changes is `stale`."""
    await rate_limit.enforce(rate_limit.UPLOAD, f"user:{ctx.user.id}")
    return await service.upload(game_id, payload, ctx.user)


@games_router.get("", response_model=GamesPageOut)
async def list_games(
    ctx: CurrentAuth,
    service: _GamesServiceDep,
    updated_since: Annotated[int | None, Query(alias="updatedSince", ge=0)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    mine: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> GamesPageOut:
    """Every game, newest change first; with `updatedSince`, deleted ones come as tombstones."""
    return await service.list_page(
        updated_since=updated_since, cursor=cursor, mine=mine, user=ctx.user, limit=limit
    )


@games_router.get("/{game_id}", response_model=GameDetailOut)
async def get_game(game_id: _GameId, _: CurrentAuth, service: _GamesServiceDep) -> GameDetailOut:
    """One game: payload, summary and achievements."""
    return await service.detail(game_id)


@games_router.delete("/{game_id}", status_code=204)
async def delete_game(game_id: _GameId, ctx: CurrentAuth, service: _GamesServiceDep) -> None:
    """Delete a game (owner or admin). Boards and achievements stop counting it."""
    await service.delete(game_id, ctx.user)
