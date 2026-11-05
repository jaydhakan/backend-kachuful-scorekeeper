"""Player registry routes (plan §6.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.api.deps import CurrentAuth, get_boards_service, get_players_service
from app.api.etag import IfNoneMatch, respond
from app.schemas.boards import CalendarOut, HeadToHeadOut, PlayerPageOut, PlayerStatsOut
from app.schemas.common import Page
from app.schemas.games import GameSummaryOut
from app.schemas.players import (
    NewAliasIn,
    NewPlayerIn,
    PhotoOut,
    PlayerCheckOut,
    PlayerOut,
    PlayerPatchIn,
)
from app.security import rate_limit
from app.services.boards import ALL_TIME_TO, BoardsService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.players import PlayersService

_PlayersServiceDep = Annotated[PlayersService, Depends(get_players_service)]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]
_BoardsServiceDep = Annotated[BoardsService, Depends(get_boards_service)]
_From = Annotated[int | None, Query(alias="from", ge=0, le=ALL_TIME_TO, description="Epoch ms")]
_To = Annotated[int | None, Query(alias="to", ge=0, le=ALL_TIME_TO, description="Epoch ms")]

players_router = APIRouter(prefix="/players", tags=["Players"])


@players_router.get("", response_model=Page[PlayerOut])
async def list_players(
    ctx: CurrentAuth,
    service: _PlayersServiceDep,
    updated_since: Annotated[int | None, Query(alias="updatedSince", ge=0)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[PlayerOut]:
    """The whole registry in sync order (oldest change first), merged players included."""
    return await service.list_page(
        updated_since=updated_since, cursor=cursor, limit=limit, viewer=ctx.user
    )


@players_router.get("/check", response_model=PlayerCheckOut)
async def check_player(
    ctx: CurrentAuth,
    service: _PlayersServiceDep,
    name: Annotated[str, Query(min_length=1, max_length=100)],
    email: Annotated[str, Query(min_length=1, max_length=254)],
) -> PlayerCheckOut:
    """Is this person already a player? A name + email match or an email match names them."""
    await rate_limit.enforce(rate_limit.PLAYER_CHECK, f"user:{ctx.user.id}")
    return await service.check(name, email)


@players_router.post("", response_model=PlayerOut, status_code=201)
async def create_player(
    body: NewPlayerIn, ctx: CurrentAuth, service: _PlayersServiceDep, response: Response
) -> PlayerOut:
    """Add a player (email required). The same id and body again answers 200."""
    player, created = await service.create_and_commit(body, ctx.user)
    if not created:
        response.status_code = 200
    return player


@players_router.patch("/{player_id}", response_model=PlayerOut)
async def update_player(
    player_id: _PlayerId, body: PlayerPatchIn, ctx: CurrentAuth, service: _PlayersServiceDep
) -> PlayerOut:
    """Edit a player: admin, the creator or the linked user may change any field."""
    return await service.patch(player_id, body, ctx.user)


@players_router.post("/{player_id}/aliases", response_model=PlayerOut, status_code=201)
async def add_player_alias(
    player_id: _PlayerId,
    body: NewAliasIn,
    ctx: CurrentAuth,
    service: _PlayersServiceDep,
    response: Response,
) -> PlayerOut:
    """Add another name for a player (any signed-in user). The same name again answers 200."""
    player, created = await service.add_alias(player_id, body, ctx.user)
    if not created:
        response.status_code = 200
    return player


@players_router.get("/{player_id}", response_model=PlayerPageOut)
async def get_player(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> PlayerPageOut | Response:
    """A player page: summary, all-time totals, achievements and global rank."""
    return respond(await service.player_page(player_id, if_none_match), response)


@players_router.get("/{player_id}/stats", response_model=PlayerStatsOut)
async def get_player_stats(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    range_from: _From = None,
    range_to: _To = None,
    if_none_match: IfNoneMatch = None,
) -> PlayerStatsOut | Response:
    """PlayerStats for a range (all time without from/to)."""
    cached = await service.player_stats(player_id, range_from, range_to, if_none_match)
    return respond(cached, response)


@players_router.get("/{player_id}/games", response_model=Page[GameSummaryOut])
async def list_player_games(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsServiceDep,
    range_from: _From = None,
    range_to: _To = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[GameSummaryOut]:
    """A player's games with a round in the range, newest first."""
    return await service.player_games(
        player_id, range_from=range_from, range_to=range_to, cursor=cursor, limit=limit
    )


@players_router.get("/{player_id}/calendar", response_model=CalendarOut)
async def get_player_calendar(
    player_id: _PlayerId,
    year: Annotated[int, Query(ge=2000, le=2099)],
    _: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> CalendarOut | Response:
    """Rounds per day (APP_TZ) for the heatmap."""
    return respond(await service.calendar(player_id, year, if_none_match), response)


@players_router.get("/{a}/vs/{b}", response_model=HeadToHeadOut)
async def get_head_to_head(
    a: _PlayerId,
    b: _PlayerId,
    _: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    range_from: _From = None,
    range_to: _To = None,
    if_none_match: IfNoneMatch = None,
) -> HeadToHeadOut | Response:
    """Two players over the games they both finished."""
    cached = await service.head_to_head(a, b, range_from, range_to, if_none_match)
    return respond(cached, response)


@players_router.put("/{player_id}/photo", response_model=PhotoOut)
async def put_player_photo(
    player_id: _PlayerId, request: Request, ctx: CurrentAuth, service: _PlayersServiceDep
) -> PhotoOut:
    """Raw WebP/JPEG/PNG bytes (at most 2 MB); stored as a 256x256 WebP without metadata."""
    data = await request.body()
    return await service.set_photo(player_id, data, request.headers.get("content-type"), ctx.user)


@players_router.delete("/{player_id}/photo", status_code=204)
async def delete_player_photo(
    player_id: _PlayerId, ctx: CurrentAuth, service: _PlayersServiceDep
) -> None:
    """Remove the photo; the player shows the emoji again."""
    await service.delete_photo(player_id, ctx.user)


@players_router.get("/{player_id}/photo", response_model=None, response_class=Response)
async def get_player_photo(player_id: _PlayerId, service: _PlayersServiceDep) -> Response:
    """Public (shared links show photos). Each version has its own URL, so it caches forever."""
    image = await service.photo(player_id)
    return Response(
        image,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
