"""Leaderboards, player pages and stats, head-to-head, calendar and bootstrap."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import GameKind
from app.domain.leaderboard import (
    Board,
    Metric,
    Tally,
    accuracy_of,
    avg_of,
    leaderboard,
    min_rounds_for,
    previous_window,
    win_rate_of,
)
from app.domain.time import day_of, day_start_ms, from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.app_state import app_state_repository
from app.repositories.boards import Window, boards_repository
from app.repositories.games import games_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.boards import (
    AsDealerOut,
    BiggestWinOut,
    BootstrapOut,
    CalendarOut,
    HeadToHeadOut,
    LeaderboardOut,
    LeaderboardRowOut,
    MadeOfOut,
    PlayerPageOut,
    PlayerStatsOut,
    RankOut,
)
from app.schemas.common import Page
from app.schemas.games import AchievementOut, GameSummaryOut
from app.services.achievements_read import achievements_for_player
from app.services.cache import Cached, etag_for, etag_matches, response_cache
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_out, player_summary_out, summary_out, user_out
from app.settings import settings

ALL_TIME_FROM = 0
ALL_TIME_TO = 4_102_444_800_000  # 2100-01-01: "no end"
SEASON_MIN_ROUNDS = 50


def make_window(start_ms: int, end_ms: int) -> Window:
    """Split [start, end) into whole APP_TZ days and the partial hours at either end."""
    tz = settings.tz
    first = day_of(start_ms, tz)
    if day_start_ms(first, tz) < start_ms:
        first += timedelta(days=1)
    end = day_of(end_ms, tz)
    start_at, end_at = from_ms(start_ms), from_ms(end_ms)
    if first >= end:
        return Window(start_at, end_at, end, end, (start_at, end_at), (start_at, start_at))
    first_at = from_ms(day_start_ms(first, tz))
    end_day_at = from_ms(day_start_ms(end, tz))
    return Window(start_at, end_at, first, end, (start_at, first_at), (end_day_at, end_at))


def check_range(range_from: int | None, range_to: int | None) -> tuple[int, int]:
    """Defaults to all time; 422 `invalid` when `from` is not before `to`."""
    start = ALL_TIME_FROM if range_from is None else range_from
    end = ALL_TIME_TO if range_to is None else range_to
    if start >= end:
        raise ApiError(
            422, ErrorCode.INVALID, "`from` must be before `to`.", fields={"from": "≥ to"}
        )
    return start, end


def stats_out(
    tally: Tally | None,
    breakdown_by: dict[str, dict[str, tuple[int, int]]],
    as_dealer: tuple[int, int],
    places: dict[int, int],
    best_game: GameSummaryOut | None,
) -> PlayerStatsOut:
    """The contract's PlayerStats from the queried parts (app: profileStats)."""
    t = tally or Tally("")
    return PlayerStatsOut(
        points=t.points,
        rounds=t.rounds,
        made=t.made,
        missed=t.rounds - t.made,
        accuracy=accuracy_of(t),
        avg_per_round=avg_of(t),
        games=t.games,
        games_ended=t.games_ended,
        wins=t.wins,
        win_rate=win_rate_of(t),
        places={str(k): v for k, v in places.items()},
        best_round=int(t.best_round) if t.rounds else None,
        worst_round=int(t.worst_round) if t.rounds else None,
        longest_hot=t.longest_hot,
        longest_cold=t.longest_cold,
        hook_hits=t.hook_hits,
        best_game=best_game,
        bid_histogram=_made_of(breakdown_by["bid"]),
        by_cards=_made_of(breakdown_by["cards"]),
        by_trump=_made_of(breakdown_by["trump"]),
        as_dealer=AsDealerOut(made=as_dealer[0], rounds=as_dealer[1]),
    )


def _made_of(counts: dict[str, tuple[int, int]]) -> dict[str, MadeOfOut]:
    return {k: MadeOfOut(made=made, total=total) for k, (made, total) in counts.items()}


class BoardsService:
    """Read-side boards and stats; every response carries a data-version ETag."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cached[T](
        self, if_none_match: str | None, key: tuple[object, ...], build: Callable[[], Awaitable[T]]
    ) -> Cached[T]:
        """A body keyed by the data version: 304 when current, else from the cache or `build`."""
        data_version = await app_state_repository.data_version(self.session)
        etag = etag_for(data_version, *key)
        if etag_matches(if_none_match, etag):
            return Cached(etag=etag, body=None)
        body: T | None = response_cache.get(etag)
        if body is None:
            body = await build()
            response_cache.put(etag, body)
        return Cached(etag=etag, body=body)

    async def board(
        self, range_from: int, range_to: int, metric: Metric, min_rounds: int | None
    ) -> Board:
        """The ranked board for a range (two tally queries: this window and the one before)."""
        minimum = min_rounds_for(range_from, range_to) if min_rounds is None else min_rounds
        current = await boards_repository.tallies(self.session, make_window(range_from, range_to))
        prev_from, prev_to = previous_window(range_from, range_to)
        previous = None
        if prev_to > prev_from:
            window = make_window(prev_from, prev_to)
            previous = (await boards_repository.tallies(self.session, window)).values()
        return leaderboard(current.values(), previous, metric, minimum)

    async def board_out(self, board: Board) -> LeaderboardOut:
        """A board with PlayerSummary rows."""
        views = await players_repository.get_views(
            self.session, [uuid.UUID(r.player_id) for r in board.rows]
        )
        rows = [
            LeaderboardRowOut(
                rank=r.rank,
                qualified=r.qualified,
                player=player_summary_out(views[uuid.UUID(r.player_id)]),
                value=r.value,
                points=r.points,
                games=r.games,
                rounds=r.rounds,
                wins=r.wins,
                accuracy=r.accuracy,
                longest_hot=r.longest_hot,
                rank_delta=r.rank_delta,
            )
            for r in board.rows
        ]
        return LeaderboardOut(rows=rows, min_rounds=board.min_rounds, generated_at=to_ms(now_utc()))

    async def leaderboard(
        self,
        *,
        range_from: int | None,
        range_to: int | None,
        metric: Metric,
        min_rounds: int | None,
        if_none_match: str | None,
        min_games: int | None = None,
        limit: int | None = None,
    ) -> Cached[LeaderboardOut]:
        """`GET /api/leaderboard`."""
        start, end = check_range(range_from, range_to)
        build = partial(self._leaderboard_body, start, end, metric, min_rounds, min_games, limit)
        key = ("board", start, end, metric, min_rounds, min_games, limit)
        return await self.cached(if_none_match, key, build)

    async def _leaderboard_body(
        self,
        start: int,
        end: int,
        metric: Metric,
        min_rounds: int | None,
        min_games: int | None,
        limit: int | None,
    ) -> LeaderboardOut:
        board = await self.board(start, end, metric, min_rounds)
        # Filtered after ranking, so a row keeps the rank it has on the full board.
        rows = [r for r in board.rows if min_games is None or r.games >= min_games]
        return await self.board_out(replace(board, rows=rows[:limit]))

    async def _view(self, player_id: uuid.UUID) -> PlayerView:
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return view

    async def stats(self, player_id: uuid.UUID, range_from: int, range_to: int) -> PlayerStatsOut:
        """PlayerStats over [from, to) (five queries, whatever the data size)."""
        window = make_window(range_from, range_to)
        tallies = await boards_repository.tallies(self.session, window, player_id)
        breakdown = await boards_repository.breakdown(self.session, player_id, window)
        places, best_id = await boards_repository.places(self.session, player_id, window)
        best = None
        if best_id is not None:
            rows = await games_repository.summaries(self.session, [best_id], kind=GameKind.KACHUFUL)
            best = summary_out(rows[best_id])
        return stats_out(
            tallies.get(str(player_id)),
            {"bid": breakdown.by_bid, "cards": breakdown.by_cards, "trump": breakdown.by_trump},
            breakdown.as_dealer,
            places,
            best,
        )

    async def player_stats(
        self,
        player_id: uuid.UUID,
        range_from: int | None,
        range_to: int | None,
        if_none_match: str | None,
    ) -> Cached[PlayerStatsOut]:
        """`GET /api/players/{id}/stats`."""
        start, end = check_range(range_from, range_to)
        await self._view(player_id)
        build = partial(self.stats, player_id, start, end)
        return await self.cached(if_none_match, ("stats", player_id, start, end), build)

    async def player_page(
        self, player_id: uuid.UUID, if_none_match: str | None
    ) -> Cached[PlayerPageOut]:
        """`GET /api/players/{id}`: summary, all-time totals, achievements and global rank."""
        view = await self._view(player_id)
        build = partial(self._page_body, view)
        return await self.cached(if_none_match, ("page", player_id), build)

    async def _page_body(self, view: PlayerView) -> PlayerPageOut:
        player_id = view.player.id
        board = await self.board(
            ALL_TIME_FROM, ALL_TIME_TO, Metric.AVG_PER_ROUND, SEASON_MIN_ROUNDS
        )
        mine = next((r for r in board.rows if r.player_id == str(player_id)), None)
        return PlayerPageOut(
            player=player_summary_out(view),
            totals=await self.stats(player_id, ALL_TIME_FROM, ALL_TIME_TO),
            achievements=await achievements_for_player(self.session, player_id),
            rank=RankOut(
                global_rank=None if mine is None else mine.rank,
                out_of=sum(1 for r in board.rows if r.qualified),
            ),
        )

    async def player_games(
        self,
        player_id: uuid.UUID,
        *,
        range_from: int | None,
        range_to: int | None,
        cursor: str | None,
        limit: int,
    ) -> Page[GameSummaryOut]:
        """`GET /api/players/{id}/games`: newest first."""
        start, end = check_range(range_from, range_to)
        await self._view(player_id)
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            before = (at, _uuid_or_invalid(key))
        found = await boards_repository.player_game_ids(
            self.session, player_id, make_window(start, end), before, limit + 1
        )
        rows = await games_repository.summaries(
            self.session, [gid for gid, _ in found[:limit]], kind=GameKind.KACHUFUL
        )
        items = [summary_out(rows[gid]) for gid, _ in found[:limit]]
        next_cursor = None
        if len(found) > limit:
            last_id, last_at = found[limit - 1]
            next_cursor = encode_cursor(last_at, str(last_id))
        return Page[GameSummaryOut](items=items, next_cursor=next_cursor)

    async def calendar(
        self, player_id: uuid.UUID, year: int, if_none_match: str | None
    ) -> Cached[CalendarOut]:
        """`GET /api/players/{id}/calendar`."""
        await self._view(player_id)
        build = partial(self._calendar_body, player_id, year)
        return await self.cached(if_none_match, ("calendar", player_id, year), build)

    async def _calendar_body(self, player_id: uuid.UUID, year: int) -> CalendarOut:
        days = await boards_repository.calendar(
            self.session, player_id, date(year, 1, 1), date(year + 1, 1, 1)
        )
        return CalendarOut(days=days)

    async def head_to_head(
        self,
        a: uuid.UUID,
        b: uuid.UUID,
        range_from: int | None,
        range_to: int | None,
        if_none_match: str | None,
    ) -> Cached[HeadToHeadOut]:
        """`GET /api/players/{a}/vs/{b}` (app: headToHead)."""
        start, end = check_range(range_from, range_to)
        view_a, view_b = await self._view(a), await self._view(b)
        build = partial(self._vs_body, view_a, view_b, make_window(start, end))
        return await self.cached(if_none_match, ("vs", a, b, start, end), build)

    async def _vs_body(
        self, view_a: PlayerView, view_b: PlayerView, window: Window
    ) -> HeadToHeadOut:
        a, b = view_a.player.id, view_b.player.id
        shared = await boards_repository.head_to_head(self.session, a, b, window)
        biggest = None
        for g in shared:
            gap = abs(g.a_points - g.b_points)
            if g.a_points != g.b_points and (biggest is None or gap > biggest[0]):
                biggest = (gap, g.game_id, a if g.a_points > g.b_points else b)
        wanted = [g.game_id for g in shared[:5]] + ([biggest[1]] if biggest else [])
        summaries = await games_repository.summaries(self.session, wanted, kind=GameKind.KACHUFUL)
        return HeadToHeadOut(
            a=player_summary_out(view_a),
            b=player_summary_out(view_b),
            games=len(shared),
            a_ahead=sum(1 for g in shared if g.a_place < g.b_place),
            b_ahead=sum(1 for g in shared if g.b_place < g.a_place),
            ties=sum(1 for g in shared if g.a_place == g.b_place),
            a_points=sum(g.a_points for g in shared),
            b_points=sum(g.b_points for g in shared),
            last_games=[summary_out(summaries[g.game_id]) for g in shared[:5]],
            biggest_win=None
            if biggest is None
            else BiggestWinOut(
                by=biggest[0], game=summary_out(summaries[biggest[1]]), winner=biggest[2]
            ),
        )

    async def bootstrap(self, user: User, if_none_match: str | None) -> Cached[BootstrapOut]:
        """`GET /api/bootstrap`: the user, the registry, their games and badges, the data version."""
        data_version = await app_state_repository.data_version(self.session)
        etag = etag_for(data_version, "bootstrap", user.id, user.player_id, user.role)
        if etag_matches(if_none_match, etag):
            return Cached(etag=etag, body=None)
        views = await players_repository.list_all_views(self.session)
        mine = next((v for v in views if v.player.id == user.player_id), None)
        games = await games_repository.owned_summaries(
            self.session, user.id, kind=GameKind.KACHUFUL
        )
        achievements: list[AchievementOut] = []
        if user.player_id is not None:
            achievements = await achievements_for_player(self.session, user.player_id)
        return Cached(
            etag=etag,
            body=BootstrapOut(
                user=user_out(user),
                player=None if mine is None else player_out(mine, user),
                players=[player_out(v, user) for v in views],
                my_games=[summary_out(g) for g in games],
                my_achievements=achievements,
                data_version=data_version,
            ),
        )


def _uuid_or_invalid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
