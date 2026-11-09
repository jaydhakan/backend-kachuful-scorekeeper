"""Kaali Teeri leaderboard, player stats and games, head-to-head and bootstrap; ETags as Kachuful's."""

import uuid
from dataclasses import asdict
from datetime import date, timedelta
from enum import StrEnum
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import GameKind
from app.domain.kaali_teeri.leaderboard import KtSort, rank_board
from app.domain.kaali_teeri.stats import head_to_head, player_stats
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.repositories.kt_boards import kt_boards_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.common import Page
from app.schemas.kaali_teeri import (
    KtBootstrapOut,
    KtGameSummaryOut,
    KtHeadToHeadOut,
    KtLeaderboardOut,
    KtLeaderboardRowOut,
    KtPlayerStatsOut,
)
from app.services.boards import BoardsService
from app.services.cache import Cached, etag_for, etag_matches
from app.services.kt_games import KIND
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import kt_summary_out, player_summary_out
from app.settings import settings

WEEK_DAYS = 7


class KtPeriod(StrEnum):
    """A board period in APP_TZ days: today, the last 7 days, this month (season), all time."""

    TODAY = "today"
    WEEK = "week"
    SEASON = "season"
    ALL = "all"


def period_days(period: KtPeriod, today: date) -> tuple[date | None, date | None]:
    """[first, end) days of a period; None bounds mean all time."""
    tomorrow = today + timedelta(days=1)
    if period == KtPeriod.TODAY:
        return today, tomorrow
    if period == KtPeriod.WEEK:
        return today - timedelta(days=WEEK_DAYS - 1), tomorrow
    if period == KtPeriod.SEASON:
        first = today.replace(day=1)
        return first, (first + timedelta(days=32)).replace(day=1)
    return None, None


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


class KtBoardsService:
    """Read-side Kaali Teeri boards and stats; every response carries a data-version ETag."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cache = BoardsService(session)

    async def _view(self, player_id: uuid.UUID) -> PlayerView:
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return view

    async def leaderboard(
        self, period: KtPeriod, sort: KtSort, if_none_match: str | None
    ) -> Cached[KtLeaderboardOut]:
        """`GET /api/kaali-teeri/leaderboard`."""
        first, end = period_days(period, now_utc().astimezone(settings.tz).date())
        build = partial(self._leaderboard_body, period, sort, first, end)
        return await self._cache.cached(if_none_match, ("kt-board", sort, first, end), build)

    async def _leaderboard_body(
        self, period: KtPeriod, sort: KtSort, first: date | None, end: date | None
    ) -> KtLeaderboardOut:
        board = rank_board(await kt_boards_repository.tallies(self.session, first, end), sort)
        views = await players_repository.get_views(
            self.session, [uuid.UUID(r.tally.player_id) for r in board]
        )
        rows = [
            KtLeaderboardRowOut(
                rank=r.rank,
                player=player_summary_out(views[uuid.UUID(r.tally.player_id)]),
                value=r.value,
                points=r.tally.points,
                games=r.tally.games,
                wins=r.tally.wins,
                hands_bid=r.tally.hands_bid,
                bids_made=r.tally.bids_made,
                made_rate=r.tally.made_rate,
            )
            for r in board
        ]
        return KtLeaderboardOut(
            period=period.value, sort=sort.value, rows=rows, generated_at=to_ms(now_utc())
        )

    async def player_stats(
        self, player_id: uuid.UUID, if_none_match: str | None
    ) -> Cached[KtPlayerStatsOut]:
        """`GET /api/kaali-teeri/players/{id}/stats`, all time."""
        await self._view(player_id)
        build = partial(self._stats_body, player_id)
        return await self._cache.cached(if_none_match, ("kt-stats", player_id), build)

    async def _stats_body(self, player_id: uuid.UUID) -> KtPlayerStatsOut:
        games = await kt_boards_repository.player_games(self.session, player_id)
        return KtPlayerStatsOut.model_validate(asdict(player_stats(games, str(player_id))))

    async def head_to_head(
        self, a: uuid.UUID, b: uuid.UUID, if_none_match: str | None
    ) -> Cached[KtHeadToHeadOut]:
        """`GET /api/kaali-teeri/players/{a}/vs/{b}`, all time."""
        view_a, view_b = await self._view(a), await self._view(b)
        build = partial(self._vs_body, view_a, view_b)
        return await self._cache.cached(if_none_match, ("kt-vs", a, b), build)

    async def _vs_body(self, view_a: PlayerView, view_b: PlayerView) -> KtHeadToHeadOut:
        a, b = view_a.player.id, view_b.player.id
        games = await kt_boards_repository.shared_games(self.session, a, b)
        counts = asdict(head_to_head(games, str(a), str(b)))
        return KtHeadToHeadOut(a=player_summary_out(view_a), b=player_summary_out(view_b), **counts)

    async def player_games(
        self, player_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> Page[KtGameSummaryOut]:
        """`GET /api/kaali-teeri/players/{id}/games`: newest created first."""
        await self._view(player_id)
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            before = (at, _cursor_id(key))
        found = await kt_boards_repository.player_game_ids(
            self.session, player_id, before, limit + 1
        )
        rows = await games_repository.summaries(
            self.session, [gid for gid, _ in found[:limit]], kind=GameKind.KAALI_TEERI
        )
        items = [kt_summary_out(rows[gid]) for gid, _ in found[:limit]]
        next_cursor = None
        if len(found) > limit:
            last_id, last_at = found[limit - 1]
            next_cursor = encode_cursor(last_at, str(last_id))
        return Page[KtGameSummaryOut](items=items, next_cursor=next_cursor)

    async def bootstrap(self, user: User, if_none_match: str | None) -> Cached[KtBootstrapOut]:
        """`GET /api/kaali-teeri/bootstrap`: the caller's live games and the data version."""
        data_version = await app_state_repository.data_version(self.session)
        etag = etag_for(data_version, "kt-bootstrap", user.id)
        if etag_matches(if_none_match, etag):
            return Cached(etag=etag, body=None)
        games = await games_repository.owned_summaries(self.session, user.id, kind=KIND)
        body = KtBootstrapOut(
            my_games=[kt_summary_out(g) for g in games], data_version=data_version
        )
        return Cached(etag=etag, body=body)
