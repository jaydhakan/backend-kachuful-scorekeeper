"""Boards, bootstrap, achievements and the activity feed (plan §6.1, §6.5)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, SessionDep, get_boards_service
from app.api.etag import IMMUTABLE, REVALIDATE, IfNoneMatch, respond
from app.domain.leaderboard import Metric
from app.schemas.awards import DailyAwardsOut, SeasonListItemOut, SeasonOut, WrappedOut
from app.schemas.boards import ActivityItemOut, BootstrapOut, LeaderboardOut
from app.schemas.common import Page
from app.schemas.games import AchievementOut
from app.services.achievements_read import achievements_page
from app.services.activity import ActivityService
from app.services.awards import AwardsService
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
    min_games: Annotated[int | None, Query(alias="minGames", ge=0, le=100_000)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> LeaderboardOut | Response:
    """Ranked players for a range; rate metrics rank only players with more than minRounds.

    minGames drops rows with fewer games, then limit keeps the first rows; ranks are unchanged by both.
    """
    cached = await service.leaderboard(
        range_from=range_from,
        range_to=range_to,
        metric=metric,
        min_rounds=min_rounds,
        min_games=min_games,
        limit=limit,
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


@boards_router.get("/awards/daily", response_model=DailyAwardsOut)
async def get_daily_awards(
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    day: Annotated[str, Query(alias="date", min_length=10, max_length=10)],
    if_none_match: IfNoneMatch = None,
) -> DailyAwardsOut | Response:
    """A day's awards (APP_TZ day). Provisional until the day is closed; then cached forever."""
    cached, final = await AwardsService(session).daily(day, if_none_match)
    return respond(cached, response, cache_control=IMMUTABLE if final else REVALIDATE)


@boards_router.get("/seasons", response_model=list[SeasonListItemOut])
async def list_seasons(
    _: CurrentAuth, session: SessionDep, response: Response, if_none_match: IfNoneMatch = None
) -> list[SeasonListItemOut] | Response:
    """Every month since the first game, newest first, with its champion."""
    return respond(await AwardsService(session).seasons(if_none_match), response)


@boards_router.get("/seasons/{season}", response_model=SeasonOut)
async def get_season(
    season: Annotated[str, Path(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")],
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> SeasonOut | Response:
    """A month's standings and awards; live during the month, frozen once closed."""
    cached, final = await AwardsService(session).season(season, if_none_match)
    return respond(cached, response, cache_control=IMMUTABLE if final else REVALIDATE)


@boards_router.get("/wrapped/{year}/{player_id}", response_model=WrappedOut)
async def get_wrapped(
    year: Annotated[int, Path(ge=2000, le=2099)],
    player_id: Annotated[uuid.UUID, Path(description="Registry player id")],
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    if_none_match: IfNoneMatch = None,
) -> WrappedOut | Response:
    """A player's year as plain data (the app animates it as story slides)."""
    return respond(await AwardsService(session).wrapped(year, player_id, if_none_match), response)
