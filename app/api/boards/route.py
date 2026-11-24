"""Boards, bootstrap, achievements and the activity feed (plan §6.1, §6.5)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import CurrentAuth, SessionDep, get_boards_service
from app.api.etag import IMMUTABLE, REVALIDATE, IfNoneMatch, respond
from app.domain.crowns import CrownKind, ReignSort
from app.domain.enums import ProgressionGame
from app.domain.leaderboard import Metric
from app.schemas.awards import (
    DailyAwardsOut,
    GameMedalsOut,
    GameSeasonOut,
    MedalsOut,
    PlacesOut,
    SeasonListItemOut,
    SeasonOut,
    WrappedOut,
)
from app.schemas.boards import ActivityItemOut, BootstrapOut, LaddersOut, LeaderboardOut
from app.schemas.common import Page
from app.schemas.crowns import CrownReignOut, CrownsOut, MilestoneOut
from app.schemas.games import AchievementOut
from app.services.achievements_read import achievements_page
from app.services.activity import ActivityService
from app.services.awards import AwardsService
from app.services.boards import ALL_TIME_TO, BoardsService
from app.services.crowns import CrownsService
from app.services.milestones import MilestonesService
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.progression.ladders import ladders

_BoardsServiceDep = Annotated[BoardsService, Depends(get_boards_service)]
_Game = Annotated[
    ProgressionGame,
    Query(description="The game: kachuful (default) | spades | kaali-teeri | ishaara"),
]
_FeedGame = Annotated[
    Literal["all", "kachuful", "spades", "kaali-teeri", "ishaara"],
    Query(description="The game (default kachuful), or `all` for every game"),
]

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
    game: _Game = ProgressionGame.KACHUFUL,
    player_id: Annotated[uuid.UUID | None, Query(alias="playerId")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[AchievementOut]:
    """One game's achievements, newest first, optionally for one player."""
    return await achievements_page(
        session, game=game, player_id=player_id, cursor=cursor, limit=limit
    )


@boards_router.get("/achievements/ladders", response_model=LaddersOut)
async def get_ladders(
    _: CurrentAuth,
    session: SessionDep,
    player_id: Annotated[uuid.UUID, Query(alias="playerId", description="Registry player id")],
    game: _Game = ProgressionGame.KACHUFUL,
) -> LaddersOut:
    """Each of a game's ladders for one player, counted the way its codes are earned."""
    return await ladders(session, game=game, player_id=player_id)


@boards_router.get("/activity", response_model=Page[ActivityItemOut])
async def list_activity(
    ctx: CurrentAuth,
    session: SessionDep,
    game: _FeedGame = "kachuful",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[ActivityItemOut]:
    """The feed: achievements, finished games, closed seasons and new players."""
    feed = None if game == "all" else ProgressionGame(game)
    return await ActivityService(session).page(feed, cursor, limit, ctx.user)


@boards_router.get("/awards/daily", response_model=DailyAwardsOut)
async def get_daily_awards(
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    day: Annotated[str, Query(alias="date", min_length=10, max_length=10)],
    game: _Game = ProgressionGame.KACHUFUL,
    if_none_match: IfNoneMatch = None,
) -> DailyAwardsOut | Response:
    """A game's awards for a day (APP_TZ). Provisional until the day is closed; then cached forever."""
    cached, final = await AwardsService(session).daily(day, if_none_match, game)
    return respond(cached, response, cache_control=IMMUTABLE if final else REVALIDATE)


@boards_router.get("/seasons", response_model=list[SeasonListItemOut])
async def list_seasons(
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    game: _Game = ProgressionGame.KACHUFUL,
    if_none_match: IfNoneMatch = None,
) -> list[SeasonListItemOut] | Response:
    """Every month since the game's first, newest first, with its champion."""
    return respond(await AwardsService(session).seasons(if_none_match, game), response)


@boards_router.get("/seasons/{season}", response_model=SeasonOut | GameSeasonOut)
async def get_season(
    season: Annotated[str, Path(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")],
    _: CurrentAuth,
    session: SessionDep,
    response: Response,
    game: _Game = ProgressionGame.KACHUFUL,
    if_none_match: IfNoneMatch = None,
) -> SeasonOut | GameSeasonOut | Response:
    """A month's standings and awards; live during the month, frozen once closed.

    Kachuful's rank points per round (`SeasonOut`); every other game's rank win rate
    (`GameSeasonOut`, at least `minGames` finished games: Spades 10, Kaali Teeri 8, Ishaara 8).
    """
    cached, final = await AwardsService(session).season(season, if_none_match, game)
    return respond(cached, response, cache_control=IMMUTABLE if final else REVALIDATE)


@boards_router.get("/medals", response_model=MedalsOut | GameMedalsOut)
async def get_medals(
    _: CurrentAuth,
    session: SessionDep,
    game: _Game = ProgressionGame.KACHUFUL,
    exclude_game: Annotated[
        uuid.UUID | None,
        Query(alias="excludeGame", description="A game to leave out (the one at the table)"),
    ] = None,
) -> MedalsOut | GameMedalsOut:
    """A game's medals: this month's board without the game in progress, last month's top three
    (who keep their medals until three players qualify this month), and today's Sitara rows.

    Kachuful ranks its points board (`MedalsOut`, `minRounds`); every other game its win-rate
    season standings (`GameMedalsOut`, `minGames`), and its `today` counts finished games and wins.
    """
    return await AwardsService(session).medals(game, exclude_game)


@boards_router.get("/crowns", response_model=CrownsOut)
async def get_crowns(
    _: CurrentAuth, session: SessionDep, game: _Game = ProgressionGame.KACHUFUL
) -> CrownsOut:
    """Every title of a game now: medal1-3, sitara, legend (holders empty when nobody)."""
    return await CrownsService(session).crowns(game)


@boards_router.get("/crowns/reigns", response_model=Page[CrownReignOut])
async def list_crown_reigns(
    _: CurrentAuth,
    session: SessionDep,
    game: _Game = ProgressionGame.KACHUFUL,
    kind: Annotated[CrownKind | None, Query(description="One title; default every title")] = None,
    sort: Annotated[
        ReignSort, Query(description="recent (newest end first) | defences (most first)")
    ] = "recent",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[CrownReignOut]:
    """Closed reigns of a game's titles."""
    return await CrownsService(session).reigns(
        game, kind=kind, sort=sort, cursor=cursor, limit=limit
    )


@boards_router.get("/milestones", response_model=Page[MilestoneOut])
async def list_milestones(
    _: CurrentAuth,
    session: SessionDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[MilestoneOut]:
    """The group's milestones and firsts, app-wide, newest first."""
    return await MilestonesService(session).page(cursor=cursor, limit=limit)


@boards_router.get("/places", response_model=PlacesOut)
async def get_places(
    _: CurrentAuth,
    session: SessionDep,
    game: _Game = ProgressionGame.KACHUFUL,
    exclude_game: Annotated[
        uuid.UUID | None,
        Query(alias="excludeGame", description="A game to leave out (the one at the table)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> PlacesOut:
    """A game's ended games' places, newest first: the app finds rivalries and milestones in them.

    A team game (Spades, Ishaara) places a side: 1 for the winners, 2 for the rest (a draw: both 2).
    """
    return await AwardsService(session).places(game, exclude_game, limit)


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
