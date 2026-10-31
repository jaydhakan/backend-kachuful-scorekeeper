"""Boards, bootstrap, achievements and the activity feed (plan §6.1, §6.5)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import CurrentAuth, SessionDep, get_boards_service
from app.api.etag import IfNoneMatch, respond
from app.domain.leaderboard import Metric
from app.schemas.boards import ActivityItemOut, BootstrapOut, LeaderboardOut
from app.schemas.common import Page
from app.schemas.games import AchievementOut
from app.services.achievements_read import achievements_page
from app.services.activity import ActivityService
from app.services.boards import ALL_TIME_TO, BoardsService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

_BoardsServiceDep = Annotated[BoardsService, Depends(get_boards_service)]

boards_router = APIRouter(tags=["Boards"])


@boards_router.get("/leaderboard", response_model=LeaderboardOut)
async def get_leaderboard(
    _: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
    range_from: Annotated[int | None, Query(alias="from", ge=0, le=ALL_TIME_TO)] = None,
    range_to: Annotated[int | None, Query(alias="to", ge=0, le=ALL_TIME_TO)] = None,
    metric: Metric = Metric.AVG_PER_ROUND,
    min_rounds: Annotated[int | None, Query(alias="minRounds", ge=0, le=100_000)] = None,
) -> LeaderboardOut | Response:
    """Ranked players for a range; rate metrics rank only players with more than minRounds."""
    cached = await service.leaderboard(
        range_from=range_from,
        range_to=range_to,
        metric=metric,
        min_rounds=min_rounds,
        if_none_match=if_none_match,
    )
    return respond(cached, response)


@boards_router.get("/bootstrap", response_model=BootstrapOut)
async def get_bootstrap(
    ctx: CurrentAuth,
    service: _BoardsServiceDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> BootstrapOut | Response:
    """Everything the app needs after sign-in or start-up, in one call."""
    return respond(await service.bootstrap(ctx.user, if_none_match), response)


@boards_router.get("/achievements", response_model=Page[AchievementOut])
async def list_achievements(
    _: CurrentAuth,
    session: SessionDep,
    player_id: Annotated[uuid.UUID | None, Query(alias="playerId")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[AchievementOut]:
    """Achievements, newest first, optionally for one player."""
    return await achievements_page(session, player_id=player_id, cursor=cursor, limit=limit)


@boards_router.get("/activity", response_model=Page[ActivityItemOut])
async def list_activity(
    _: CurrentAuth,
    session: SessionDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[ActivityItemOut]:
    """The feed: achievements, finished games, closed seasons and new players."""
    return await ActivityService(session).page(cursor, limit)
