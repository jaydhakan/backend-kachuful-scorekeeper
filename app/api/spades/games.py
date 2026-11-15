"""Spades table-mode routes: sync and bootstrap (kind `spades`); leaderboard and player stats read
both kinds. Share links stay on /games."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, get_spades_boards_service, get_spades_games_service
from app.api.etag import IfNoneMatch, respond
from app.schemas.common import Page
from app.schemas.games import UploadOut
from app.schemas.spades import (
    SpBootstrapOut,
    SpGameDetailOut,
    SpGamesPageOut,
    SpHeadToHeadOut,
    SpLeaderboardOut,
    SpPlayerStatsOut,
)
from app.security import rate_limit
from app.services.kt_boards import KtPeriod
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.spades_boards import SpadesBoardsService, SpGameOrTable, SpSort
from app.services.spades_games import SpadesGamesService

_GamesDep = Annotated[SpadesGamesService, Depends(get_spades_games_service)]
_BoardsDep = Annotated[SpadesBoardsService, Depends(get_spades_boards_service)]
_GameId = Annotated[uuid.UUID, Path(description="The game's sync id")]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]

spades_games_router = APIRouter()


@spades_games_router.put(
    "/games/{game_id}", response_model=UploadOut, response_model_exclude_none=True
)
async def upload_spades_game(
    game_id: _GameId,
    payload: Annotated[dict[str, Any], Body(description="The app's SpGame (table mode)")],
    ctx: CurrentAuth,
    service: _GamesDep,
) -> UploadOut:
    """Store a table-mode game. Same payload again is a no-op; an older or equal rev is `stale`.

    `earned` is always empty. An id stored as another game's is `409 kind_mismatch`; a failed rule
    is `422 invalid_game {reason, handIndex}`. A game with no hand done is not stored (and an
    older copy is deleted).
    """
    await rate_limit.enforce(rate_limit.UPLOAD, f"user:{ctx.user.id}")
    return await service.upload(game_id, payload, ctx.user)


@spades_games_router.get("/games", response_model=SpGamesPageOut)
async def list_spades_games(
    ctx: CurrentAuth,
    service: _GamesDep,
    updated_since: Annotated[int | None, Query(alias="updatedSince", ge=0)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    mine: Annotated[bool, Query()] = False,
    involving: Annotated[
        bool, Query(description="Games the caller scored or sat in (wins over `mine`)")
    ] = False,
    status: Annotated[
        Literal["active"] | None,
        Query(description="`active`: only unfinished games (not ended, not deleted)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SpGamesPageOut:
    """Every table-mode game, newest change first; with `updatedSince`, deleted ones as tombstones."""
    return await service.list_page(
        updated_since=updated_since,
        cursor=cursor,
        mine=mine,
        involving=involving,
        active_only=status == "active",
        user=ctx.user,
        limit=limit,
    )


@spades_games_router.get("/games/{game_id}", response_model=SpGameDetailOut)
async def get_spades_game(game_id: _GameId, _: CurrentAuth, service: _GamesDep) -> SpGameDetailOut:
    """One table-mode game: payload and summary."""
    return await service.detail(game_id)


@spades_games_router.delete("/games/{game_id}", status_code=204)
async def delete_spades_game(game_id: _GameId, ctx: CurrentAuth, service: _GamesDep) -> None:
    """Delete a table-mode game (owner or admin). Boards and stats stop counting it; links go."""
    await service.delete(game_id, ctx.user)


@spades_games_router.get("/bootstrap", response_model=SpBootstrapOut)
async def get_spades_bootstrap(
    ctx: CurrentAuth, service: _BoardsDep, response: Response, if_none_match: IfNoneMatch = None
) -> SpBootstrapOut | Response:
    """The caller's table-mode games; players come from `GET /api/bootstrap`."""
    return respond(await service.bootstrap(ctx.user, if_none_match), response)


@spades_games_router.get("/leaderboard", response_model=SpLeaderboardOut)
async def get_spades_leaderboard(
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
    period: KtPeriod = KtPeriod.ALL,
    sort: SpSort = SpSort.WINS,
) -> SpLeaderboardOut | Response:
    """Players of both kinds ranked over APP_TZ days a game finished: today, week, season or all.

    A draw is played, not won; cancelled games count nowhere. `rate` ranks from 3 games and `made`
    from one bid; the rest come last with a null rank. `pairs` are the best partner pairs.
    """
    return respond(await service.leaderboard(period, sort, if_none_match), response)


@spades_games_router.get("/players/{player_id}/stats", response_model=SpPlayerStatsOut)
async def get_spades_player_stats(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> SpPlayerStatsOut | Response:
    """A player's Spades totals, rates, best score and partners over both kinds, all time."""
    return respond(await service.player_stats(player_id, if_none_match), response)


@spades_games_router.get("/players/{player_id}/games", response_model=Page[SpGameOrTable])
async def list_spades_player_games(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[SpGameOrTable]:
    """Table-mode games and online tables a player sat at, newest started first (`kind` tells)."""
    return await service.player_games(player_id, cursor=cursor, limit=limit)


@spades_games_router.get("/players/{a}/vs/{b}", response_model=SpHeadToHeadOut)
async def get_spades_head_to_head(
    a: _PlayerId,
    b: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> SpHeadToHeadOut | Response:
    """Two players across the finished games both played to the end, as partners or opponents."""
    return respond(await service.head_to_head(a, b, if_none_match), response)
