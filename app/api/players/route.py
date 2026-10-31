"""Player registry routes (plan §6.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, get_boards_service, get_players_service
from app.api.etag import IfNoneMatch, respond
from app.schemas.boards import CalendarOut, HeadToHeadOut, PlayerPageOut, PlayerStatsOut
from app.schemas.common import Page
from app.schemas.games import GameSummaryOut
from app.schemas.players import NameCheckOut, NewPlayerIn, PlayerOut, PlayerPatchIn
from app.services.boards import ALL_TIME_TO, BoardsService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.players import PlayersService
from app.services.presenters import player_out

_PlayersServiceDep = Annotated[PlayersService, Depends(get_players_service)]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]
_BoardsServiceDep = Annotated[BoardsService, Depends(get_boards_service)]
_From = Annotated[int | None, Query(alias="from", ge=0, le=ALL_TIME_TO, description="Epoch ms")]
_To = Annotated[int | None, Query(alias="to", ge=0, le=ALL_TIME_TO, description="Epoch ms")]

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
