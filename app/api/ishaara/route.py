"""Ishaara routes: the server runs the game. The key has its own route and is never anywhere else."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentAuth,
    get_ishaara_boards_service,
    get_ishaara_games_service,
    get_ishaara_rematch_service,
)
from app.api.etag import IfNoneMatch, respond
from app.db.session import get_session_factory
from app.live.hub import hub
from app.live.sse import game_stream
from app.schemas.ishaara import (
    ActionIn,
    CreateIshaaraIn,
    IshaaraGameOut,
    IshaaraHeadToHeadOut,
    IshaaraKeyOut,
    IshaaraLeaderboardOut,
    IshaaraPageOut,
    IshaaraPlayerStatsOut,
    RematchIn,
)
from app.security import rate_limit
from app.services.ishaara_boards import IshaaraBoardsService
from app.services.ishaara_games import IshaaraGamesService, load_game_event
from app.services.ishaara_rematch import IshaaraRematchService
from app.services.kt_boards import KtPeriod
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

hub.set_game_loader(load_game_event)

_GamesDep = Annotated[IshaaraGamesService, Depends(get_ishaara_games_service)]
_RematchDep = Annotated[IshaaraRematchService, Depends(get_ishaara_rematch_service)]
_BoardsDep = Annotated[IshaaraBoardsService, Depends(get_ishaara_boards_service)]
_GameId = Annotated[uuid.UUID, Path(description="Game id")]
_PlayerId = Annotated[uuid.UUID, Path(description="Registry player id")]

ishaara_router = APIRouter(prefix="/ishaara", tags=["Ishaara"])


@ishaara_router.post("/games", response_model=IshaaraGameOut, status_code=201)
async def create_ishaara_game(
    body: CreateIshaaraIn, ctx: CurrentAuth, service: _GamesDep, response: Response
) -> IshaaraGameOut:
    """Create a game: the server deals the key and the starting team. Waits for both clue-givers.

    The same host re-sending the same id gets the stored game with 200.
    """
    await rate_limit.enforce(rate_limit.ISHAARA_CREATE, f"user:{ctx.user.id}")
    out, created = await service.create(body, ctx.user)
    if not created:
        response.status_code = 200
    return out


@ishaara_router.get("/games", response_model=IshaaraPageOut)
async def list_ishaara_games(
    ctx: CurrentAuth,
    service: _GamesDep,
    role: Annotated[
        Literal["clue-giver"] | None, Query(description="Only games the caller gives clues in")
    ] = None,
    status: Annotated[
        Literal["active", "finished"] | None,
        Query(description="`active`: waiting or in play; `finished`: over"),
    ] = None,
    involving: Annotated[
        uuid.UUID | None, Query(description="Games this player sits in (admin: anyone's)")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> IshaaraPageOut:
    """Games the caller hosts or sits in, newest change first. Summaries only, never the key."""
    return await service.list_page(
        ctx.user,
        clue_giver=role == "clue-giver",
        status=status,
        involving=involving,
        cursor=cursor,
        limit=limit,
    )


@ishaara_router.get("/games/{game_id}", response_model=IshaaraGameOut)
async def get_ishaara_game(
    game_id: _GameId, ctx: CurrentAuth, service: _GamesDep
) -> IshaaraGameOut:
    """The public state (members and the admin; 404 for everyone else)."""
    return await service.get(game_id, ctx.user)


@ishaara_router.get("/games/{game_id}/key", response_model=IshaaraKeyOut)
async def get_ishaara_key(
    game_id: _GameId, ctx: CurrentAuth, service: _GamesDep, response: Response
) -> IshaaraKeyOut:
    """The key, for the two clue-giver accounts only (403 for the host and admin otherwise).

    The first open by a team marks it joined; the game starts once both have.
    """
    response.headers["Cache-Control"] = "no-store"
    return await service.key(game_id, ctx.user)


@ishaara_router.post("/games/{game_id}/actions", response_model=IshaaraGameOut)
async def act_in_ishaara_game(
    game_id: _GameId, body: ActionIn, ctx: CurrentAuth, service: _GamesDep
) -> IshaaraGameOut:
    """Apply one action. A known `actionId` is a no-op returning the current state.

    `409 stale`, `403 not_your_turn` and `422 invalid_action {reason}` carry the current `game`.
    """
    await rate_limit.enforce(rate_limit.ISHAARA_ACTION, f"game:{game_id}")
    return await service.act(game_id, body, ctx.user)


@ishaara_router.get(
    "/games/{game_id}/events", response_model=None, response_class=StreamingResponse
)
async def ishaara_events(
    game_id: _GameId,
    ctx: CurrentAuth,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE: `state` first and on every change (id = rev), `joined`, `ended`, `ping` every 25 s."""
    queue = await hub.subscribe_game(game_id)
    try:
        # Own short session: a stream can last an hour and must not hold a pooled connection.
        async with get_session_factory()() as session:
            state = await IshaaraGamesService(session).follow(game_id, ctx.user)
    except BaseException:
        hub.unsubscribe_game(game_id, queue)
        raise
    seen = last_event_id is not None and last_event_id.isdigit()
    first = None if seen and int(str(last_event_id)) >= (state.rev or 0) else state
    return StreamingResponse(
        game_stream(game_id, queue, first),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@ishaara_router.delete("/games/{game_id}", status_code=204)
async def delete_ishaara_game(game_id: _GameId, ctx: CurrentAuth, service: _GamesDep) -> None:
    """Soft delete (host or admin); it is gone from every read and its followers' streams end."""
    await service.delete(game_id, ctx.user)


@ishaara_router.post("/games/{game_id}/rematch", response_model=IshaaraGameOut, status_code=201)
async def rematch_ishaara_game(
    game_id: _GameId, body: RematchIn, ctx: CurrentAuth, service: _RematchDep, response: Response
) -> IshaaraGameOut:
    """The next game (host): same seats and rules, fresh words, the other team starts.

    200 with the existing game when this one already has a rematch.
    """
    out, created = await service.rematch(game_id, body, ctx.user)
    if not created:
        response.status_code = 200
    return out


@ishaara_router.get("/leaderboard", response_model=IshaaraLeaderboardOut)
async def get_ishaara_leaderboard(
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
    period: KtPeriod = KtPeriod.ALL,
    sort: Literal["wins", "rate", "found"] = "wins",
) -> IshaaraLeaderboardOut | Response:
    """Players ranked over finished games by APP_TZ day; `rate` ranks only 3+ games."""
    return respond(await service.leaderboard(period, sort, if_none_match), response)


@ishaara_router.get("/players/{player_id}/stats", response_model=IshaaraPlayerStatsOut)
async def get_ishaara_player_stats(
    player_id: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> IshaaraPlayerStatsOut | Response:
    """A player's Ishaara totals over all finished games (the domain's tally)."""
    return respond(await service.player_stats(player_id, if_none_match), response)


@ishaara_router.get("/players/{a}/vs/{b}", response_model=IshaaraHeadToHeadOut)
async def get_ishaara_head_to_head(
    a: _PlayerId,
    b: _PlayerId,
    _: CurrentAuth,
    service: _BoardsDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> IshaaraHeadToHeadOut | Response:
    """Two players across finished games both sat in: as teammates and as opponents."""
    return respond(await service.head_to_head(a, b, if_none_match), response)
