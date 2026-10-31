"""Daily awards, seasons and Wrapped (plan §8.4-8.5). Closed days and seasons are frozen."""

import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.achievements import Earned
from app.domain.awards import SEASON_MIN_ROUNDS, Award, daily_awards, season_awards
from app.domain.leaderboard import Board, BoardRow, Metric
from app.domain.time import (
    day_of,
    day_range_ms,
    month_of,
    month_range_ms,
    now_utc,
    previous_month,
    to_ms,
)
from app.exceptions import ApiError, ErrorCode
from app.repositories.achievements import achievements_repository
from app.repositories.app_state import app_state_repository
from app.repositories.boards import boards_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.awards import (
    AwardOut,
    BiggestRoundOut,
    DailyAwardsOut,
    DayPointsOut,
    DayRoundsOut,
    MonthPointsOut,
    NemesisOut,
    PartnerOut,
    SeasonListItemOut,
    SeasonOut,
    TrumpOut,
    WrappedOut,
    WrappedRankOut,
)
from app.schemas.players import PlayerSummaryOut
from app.services.achievements import activity_rows
from app.services.boards import BoardsService, make_window
from app.services.cache import Cached, etag_for, etag_matches
from app.services.presenters import player_summary_out
from app.settings import settings


def parse_day(raw: str) -> date:
    """A YYYY-MM-DD date; 422 `invalid` otherwise."""
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Use YYYY-MM-DD.", fields={"date": "YYYY-MM-DD"}
        ) from exc


def parse_month(raw: str) -> str:
    """A YYYY-MM month; 422 `invalid` otherwise."""
    try:
        date.fromisoformat(f"{raw}-01")
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Use YYYY-MM.", fields={"season": "YYYY-MM"}
        ) from exc
    return raw


def today() -> date:
    """Today in APP_TZ."""
    return day_of(to_ms(now_utc()), settings.tz)


def _award_dict(award: Award) -> dict[str, Any]:
    return {
        "code": award.code,
        "playerId": award.player_id,
        "value": award.value,
        "gameId": award.game_id,
        "roundIndex": award.round_index,
    }


def _award_from_dict(raw: dict[str, Any]) -> Award:
    return Award(
        raw["code"], raw["playerId"], raw["value"], raw.get("gameId"), raw.get("roundIndex")
    )


def _board_row_dict(row: BoardRow) -> dict[str, Any]:
    return asdict(row)


def award_earned(award: Award, period: str, earned_at: int) -> Earned:
    """An award as an achievements row for its day or season."""
    return Earned(
        code=award.code,
        player_id=award.player_id,
        game_id=award.game_id,
        round_index=award.round_index,
        period=period,
        earned_at=earned_at,
        data={"value": award.value},
    )


class AwardsService:
    """Daily awards, seasons and Wrapped."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.boards = BoardsService(session)

    async def compute_daily(self, day: date) -> list[Award]:
        """The day's awards from its rounds (two queries)."""
        window = make_window(*day_range_ms(day, settings.tz))
        rounds = await boards_repository.day_rounds(self.session, window)
        tallies = await boards_repository.tallies(self.session, window)
        return daily_awards(rounds, tallies.values())

    async def compute_season(self, month: str) -> tuple[Board, list[Award]]:
        """A month's standings (more than 50 rounds) and its awards."""
        start, end = month_range_ms(month, settings.tz)
        board = await self.boards.board(start, end, Metric.AVG_PER_ROUND, SEASON_MIN_ROUNDS)
        prev_start, prev_end = month_range_ms(previous_month(month), settings.tz)
        last = await self.boards.board(
            prev_start, prev_end, Metric.AVG_PER_ROUND, SEASON_MIN_ROUNDS
        )
        return board, season_awards(board, last)

    async def _views(self, ids: set[str]) -> dict[str, PlayerView]:
        """Players by id, following merges to the player that remains."""
        views = await players_repository.get_views(self.session, [uuid.UUID(i) for i in ids])
        targets = {v.player.merged_into for v in views.values() if v.player.merged_into}
        merged = await players_repository.get_views(self.session, [t for t in targets if t])
        return {
            str(pid): merged.get(v.player.merged_into, v) if v.player.merged_into else v
            for pid, v in views.items()
        }

    async def _awards_out(self, awards: list[Award]) -> list[AwardOut]:
        views = await self._views({a.player_id for a in awards})
        return [
            AwardOut(
                code=a.code,
                player=player_summary_out(views[a.player_id]),
                value=a.value,
                game_id=None if a.game_id is None else uuid.UUID(a.game_id),
                round_index=a.round_index,
            )
            for a in awards
        ]

    async def daily(
        self, raw_day: str, if_none_match: str | None
    ) -> tuple[Cached[DailyAwardsOut], bool]:
        """`GET /api/awards/daily`; the flag is True when the day is closed (cache forever)."""
        day = parse_day(raw_day)
        closed = await achievements_repository.closed(self.session, day.isoformat())
        if closed is not None:
            etag = etag_for(0, "closed-day", day.isoformat())
            if etag_matches(if_none_match, etag):
                return Cached(etag=etag, body=None), True
            awards = [_award_from_dict(a) for a in closed.data["awards"]]
            body = DailyAwardsOut(
                date=day.isoformat(), final=True, awards=await self._awards_out(awards)
            )
            return Cached(etag=etag, body=body), True
        cached = await self.boards.cached(
            if_none_match, ("day", day), lambda: self._live_daily(day)
        )
        return cached, False

    async def _live_daily(self, day: date) -> DailyAwardsOut:
        awards = await self.compute_daily(day) if day <= today() else []
        return DailyAwardsOut(
            date=day.isoformat(), final=False, awards=await self._awards_out(awards)
        )

    async def season(
        self, raw_month: str, if_none_match: str | None
    ) -> tuple[Cached[SeasonOut], bool]:
        """`GET /api/seasons/{yyyy-mm}`; the flag is True once the season is closed."""
        month = parse_month(raw_month)
        closed = await achievements_repository.closed(self.session, month)
        if closed is not None:
            etag = etag_for(0, "closed-season", month)
            if etag_matches(if_none_match, etag):
                return Cached(etag=etag, body=None), True
            rows = [BoardRow(**r) for r in closed.data["standings"]]
            awards = [_award_from_dict(a) for a in closed.data["awards"]]
            board = Board(rows=rows, min_rounds=SEASON_MIN_ROUNDS)
            body = await self._season_out(month, True, board, awards)
            return Cached(etag=etag, body=body), True
        cached = await self.boards.cached(
            if_none_match, ("season", month), lambda: self._live_season(month)
        )
        return cached, False

    async def _live_season(self, month: str) -> SeasonOut:
        board, awards = await self.compute_season(month)
        return await self._season_out(month, False, board, awards)

    async def _season_out(
        self, month: str, final: bool, board: Board, awards: list[Award]
    ) -> SeasonOut:
        standings = await self.boards.board_out(board)
        return SeasonOut(
            season=month,
            final=final,
            standings=standings.rows,
            awards=await self._awards_out(awards),
        )

    async def seasons(self, if_none_match: str | None) -> Cached[list[SeasonListItemOut]]:
        """`GET /api/seasons`: every month since the first game, newest first."""
        return await self.boards.cached(if_none_match, ("seasons",), self._seasons_body)

    async def _seasons_body(self) -> list[SeasonListItemOut]:
        first = await boards_repository.first_played_day(self.session)
        if first is None:
            return []
        closed = {c.period: c for c in await achievements_repository.closed_seasons(self.session)}
        months: list[str] = []
        cursor = date(first.year, first.month, 1)
        current = today()
        while cursor <= current:
            months.append(cursor.strftime("%Y-%m"))
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        champions: dict[str, str | None] = {}
        for month in months:
            if month in closed:
                found = [a for a in closed[month].data["awards"] if a["code"] == "season_champion"]
                champions[month] = found[0]["playerId"] if found else None
            else:
                board, _ = await self.compute_season(month)
                top = next((r for r in board.rows if r.rank == 1), None)
                champions[month] = None if top is None else top.player_id
        views = await self._views({pid for pid in champions.values() if pid})
        return [
            SeasonListItemOut(season=month, champion=_champion(views, champions[month]))
            for month in reversed(months)
        ]

    async def close_day(self, day: date) -> list[Award] | None:
        """Freeze a past day's awards (None when it was already closed). Does not commit."""
        period = day.isoformat()
        if await achievements_repository.closed(self.session, period) is not None:
            return None
        awards = await self.compute_daily(day)
        _, end = day_range_ms(day, settings.tz)
        await self._record(awards, period, end - 1)
        await achievements_repository.close(
            self.session, period, "day", {"awards": [_award_dict(a) for a in awards]}
        )
        await app_state_repository.bump(self.session)
        return awards

    async def close_season(self, month: str) -> list[Award] | None:
        """Freeze a past month's standings and awards (None when already closed). No commit."""
        if await achievements_repository.closed(self.session, month) is not None:
            return None
        board, awards = await self.compute_season(month)
        _, end = month_range_ms(month, settings.tz)
        await self._record(awards, month, end - 1)
        champions = [a.player_id for a in awards if a.code == "season_champion"]
        await achievements_repository.add_activity(
            self.session,
            [{"kind": "season_closed", "data": {"season": month, "champions": champions}}],
        )
        await achievements_repository.close(
            self.session,
            month,
            "season",
            {
                "standings": [_board_row_dict(r) for r in board.rows],
                "awards": [_award_dict(a) for a in awards],
            },
        )
        await app_state_repository.bump(self.session)
        return awards

    async def _record(self, awards: list[Award], period: str, earned_at: int) -> None:
        rows = await achievements_repository.insert(
            self.session, [award_earned(a, period, earned_at) for a in awards]
        )
        await achievements_repository.add_activity(self.session, activity_rows(rows))

    async def wrapped(
        self, year: int, player_id: uuid.UUID, if_none_match: str | None
    ) -> Cached[WrappedOut]:
        """`GET /api/wrapped/{year}/{playerId}`."""
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return await self.boards.cached(
            if_none_match, ("wrapped", year, player_id), lambda: self._wrapped_body(year, view)
        )

    async def _wrapped_body(self, year: int, view: PlayerView) -> WrappedOut:
        player_id = view.player.id
        start, _ = month_range_ms(f"{year}-01", settings.tz)
        _, end = month_range_ms(f"{year}-12", settings.tz)
        window = make_window(start, end)
        totals = await self.boards.stats(player_id, start, end)
        days = await boards_repository.player_days(
            self.session, player_id, date(year, 1, 1), date(year + 1, 1, 1)
        )
        months: dict[str, int] = {}
        for day, points, _ in days:
            months[day.strftime("%Y-%m")] = months.get(day.strftime("%Y-%m"), 0) + points
        best_month = _first_max(sorted(months.items()), lambda m: m[1])
        best_day = _first_max(days, lambda d: d[1])
        trumps = sorted((t, m.made, m.total) for t, m in totals.by_trump.items() if m.total >= 10)
        trump = _first_max(trumps, lambda t: t[1] / t[2])
        opponents = await boards_repository.opponents(self.session, player_id, window)
        nemesis = _first_max([o for o in opponents if o[2] > 0], lambda o: o[2])
        partner = _first_max(opponents, lambda o: o[1])
        biggest = await boards_repository.biggest_round(self.session, player_id, window)
        board = await self.boards.board(start, end, Metric.AVG_PER_ROUND, SEASON_MIN_ROUNDS)
        mine = next((r for r in board.rows if r.player_id == str(player_id)), None)
        others = await self._views({str(o[0]) for o in (nemesis, partner) if o is not None})
        top_days = sorted(days, key=lambda d: (-d[2], d[0]))[:3]
        return WrappedOut(
            year=year,
            player_id=player_id,
            player=player_summary_out(view),
            totals=totals,
            best_month=None
            if best_month is None
            else MonthPointsOut(month=best_month[0], points=best_month[1]),
            best_day=None
            if best_day is None
            else DayPointsOut(day=best_day[0].isoformat(), points=best_day[1]),
            favourite_trump=None
            if trump is None
            else TrumpOut(trump=trump[0], accuracy=trump[1] / trump[2], rounds=trump[2]),
            nemesis=None
            if nemesis is None
            else NemesisOut(
                player_id=nemesis[0],
                player=player_summary_out(others[str(nemesis[0])]),
                losses=nemesis[2],
            ),
            partner=None
            if partner is None
            else PartnerOut(
                player_id=partner[0],
                player=player_summary_out(others[str(partner[0])]),
                games=partner[1],
            ),
            biggest_round=None
            if biggest is None
            else BiggestRoundOut(points=biggest[0], game_id=biggest[1], round_index=biggest[2]),
            longest_run=totals.longest_hot,
            achievements=await boards_repository.achievements_earned(
                self.session, player_id, window
            ),
            top_days=[DayRoundsOut(day=d.isoformat(), rounds=r) for d, _, r in top_days],
            rank=WrappedRankOut(
                rank=None if mine is None else mine.rank,
                out_of=sum(1 for r in board.rows if r.qualified),
            ),
        )


def _champion(views: dict[str, PlayerView], player_id: str | None) -> PlayerSummaryOut | None:
    return None if player_id is None else player_summary_out(views[player_id])


def _first_max[T](items: list[T], value: Callable[[T], float]) -> T | None:
    """The first item with the highest value (the app keeps the earliest on a tie)."""
    best: T | None = None
    for item in items:
        if best is None or value(item) > value(best):
            best = item
    return best


def last_month() -> str:
    """The month before the current one, in APP_TZ."""
    return previous_month(month_of(to_ms(now_utc()), settings.tz))
