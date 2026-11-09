"""Kaali Teeri routes: sync, bootstrap, leaderboard and player stats. Share links stay on /games."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, get_kt_boards_service, get_kt_games_service
from app.api.etag import IfNoneMatch, respond
from app.domain.kaali_teeri.leaderboard import KtSort
from app.schemas.common import Page
from app.schemas.games import UploadOut
from app.schemas.kaali_teeri import (
    KtBootstrapOut,
    KtGameDetailOut,
    KtGamesPageOut,
    KtGameSummaryOut,
    KtHeadToHeadOut,
    KtLeaderboardOut,
    KtPlayerStatsOut,
)
from app.security import rate_limit
from app.services.kt_boards import KtBoardsService, KtPeriod
from app.services.kt_games import KtGamesService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

_GamesDep = Annotated[KtGamesService, Depends(get_kt_games_service)]
_BoardsDep = Annotated[KtBoardsService, Depends(get_kt_boards_service)]
_GameId = Annotated[uuid.UUID, Path(description="The game's sync id")]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]

kaali_teeri_router = APIRouter(prefix="/kaali-teeri", tags=["Kaali Teeri"])


@kaali_teeri_router.put(
    "/games/{game_id}", response_model=UploadOut, response_model_exclude_none=True
)
async def upload_kt_game(
    game_id: _GameId,
    payload: Annotated[dict[str, Any], Body(description="The app's KtGame")],
    ctx: CurrentAuth,
    service: _GamesDep,
) -> UploadOut:
    """Store a game. Same payload again is a no-op; an older or equal rev with changes is `stale`.

    `earned` is always empty (Kaali Teeri has no achievements yet). An id stored as another game's
    is `409 kind_mismatch`; a failed rule is `422 invalid_game {reason, handIndex}`.
    """
    await rate_limit.enforce(rate_limit.UPLOAD, f"user:{ctx.user.id}")
    return await service.upload(game_id, payload, ctx.user)


@kaali_teeri_router.get("/games", response_model=KtGamesPageOut)
async def list_kt_games(
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
        Query(description="`active`: only unfinished games (not finished, not deleted)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> KtGamesPageOut:
    """Every game, newest change first; with `updatedSince`, deleted ones come as tombstones."""
    return await service.list_page(
        updated_since=updated_since,
        cursor=cursor,
        mine=mine,
        involving=involving,
        active_only=status == "active",
        user=ctx.user,
        limit=limit,
    )


@kaali_teeri_router.get("/games/{game_id}", response_model=KtGameDetailOut)
async def get_kt_game(game_id: _GameId, _: CurrentAuth, service: _GamesDep) -> KtGameDetailOut:
    """One game: payload and summary."""
    return await service.detail(game_id)


@kaali_teeri_router.delete("/games/{game_id}", status_code=204)
async def delete_kt_game(game_id: _GameId, ctx: CurrentAuth, service: _GamesDep) -> None:
    """Delete a game (owner or admin). Boards and stats stop counting it; its links go."""
    await service.delete(game_id, ctx.user)


@kaali_teeri_router.get("/bootstrap", response_model=KtBootstrapOut)
async def get_kt_bootstrap(
    ctx: CurrentAuth, service: _BoardsDep, response: Response, if_none_match: IfNoneMatch = None
) -> KtBootstrapOut | Response:
    """The caller's Kaali Teeri games; players come from `GET /api/bootstrap`."""
    return respond(await service.bootstrap(ctx.user, if_none_match), response)


@kaali_teeri_router.get("/leaderboard", response_model=KtLeaderboardOut)
async def get_kt_leaderboard(
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
    period: KtPeriod = KtPeriod.ALL,
    sort: KtSort = KtSort.POINTS,
) -> KtLeaderboardOut | Response:
    """Players ranked over APP_TZ days: today, week (last 7 days), season (this month) or all.

    `made` ranks by bids made / hands bid; players who never bid come last with a null rank.
    """
    return respond(await service.leaderboard(period, sort, if_none_match), response)


@kaali_teeri_router.get("/players/{player_id}/stats", response_model=KtPlayerStatsOut)
async def get_kt_player_stats(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> KtPlayerStatsOut | Response:
    """A player's Kaali Teeri profile over all their games (the app's playerStats)."""
    return respond(await service.player_stats(player_id, if_none_match), response)


@kaali_teeri_router.get("/players/{player_id}/games", response_model=Page[KtGameSummaryOut])
async def list_kt_player_games(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[KtGameSummaryOut]:
    """The games a player sat in, newest started first."""
    return await service.player_games(player_id, cursor=cursor, limit=limit)


@kaali_teeri_router.get("/players/{a}/vs/{b}", response_model=KtHeadToHeadOut)
async def get_kt_head_to_head(
    a: _PlayerId,
    b: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> KtHeadToHeadOut | Response:
    """Two players across the games both sat in (the app's headToHead)."""
    return respond(await service.head_to_head(a, b, if_none_match), response)
