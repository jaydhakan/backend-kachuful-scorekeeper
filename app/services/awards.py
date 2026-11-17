"""Daily awards, seasons and Wrapped (plan §8.4-8.5). Closed days and seasons are frozen.

Every game has daily awards (its `GameProgression`) and seasons: Kachuful's rank its points board
(`SeasonOut`), every other game's rank win rate (`GameSeasonOut`, its `WinRateSeasons`). A day
or month closes once per game.
"""

import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.awards import SEASON_MIN_ROUNDS, season_awards
from app.domain.enums import ProgressionGame
from app.domain.leaderboard import Board, BoardRow, Metric
from app.domain.progression import Award, Earned, StandingRow, win_rate_standings
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
    GameSeasonOut,
    MonthPointsOut,
    NemesisOut,
    PartnerOut,
    SeasonListItemOut,
    SeasonOut,
    StandingOut,
    TrumpOut,
    WrappedOut,
    WrappedRankOut,
)
from app.schemas.players import PlayerSummaryOut
from app.services.achievements import activity_rows
from app.services.boards import BoardsService, make_window
from app.services.cache import Cached, etag_for, etag_matches
from app.services.presenters import player_summary_out
from app.services.progression.base import WinRateSeasons
from app.services.progression.registry import progression_of, seasons_of
from app.settings import settings

KACHUFUL = ProgressionGame.KACHUFUL


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


def _scope(game: ProgressionGame) -> tuple[str, ...]:
    """Cache and ETag key parts: none for Kachuful, so its keys stay as they were."""
    return () if game == KACHUFUL else (game.value,)


def _months_since(first: date) -> list[str]:
    """Every month from `first`'s to the current one, oldest first."""
    months: list[str] = []
    cursor = date(first.year, first.month, 1)
    current = today()
    while cursor <= current:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months


def _award_out(view: PlayerView, a: Award) -> AwardOut:
    return AwardOut(
        code=a.code,
        player=player_summary_out(view),
        value=a.value,
        game_id=None if a.game_id is None else uuid.UUID(a.game_id),
        round_index=a.round_index,
    )


def _standing_out(view: PlayerView, r: StandingRow) -> StandingOut:
    return StandingOut(
        rank=r.rank,
        qualified=r.qualified,
        player=player_summary_out(view),
        value=r.value,
        games=r.games,
        wins=r.wins,
    )


def _top(standings: list[StandingRow]) -> str | None:
    top = next((r for r in standings if r.rank == 1), None)
    return None if top is None else top.player_id


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

    async def compute_daily(self, day: date, game: ProgressionGame = KACHUFUL) -> list[Award]:
        """One game's awards for a day (Kachuful: from its rounds, two queries)."""
        return await progression_of(game).daily_awards(self.session, day)

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
        return [_award_out(views[a.player_id], a) for a in awards]

    async def daily(
        self, raw_day: str, if_none_match: str | None, game: ProgressionGame = KACHUFUL
    ) -> tuple[Cached[DailyAwardsOut], bool]:
        """`GET /api/awards/daily`; the flag is True when the day is closed (cache forever)."""
        day = parse_day(raw_day)
        closed = await achievements_repository.closed(self.session, game, day.isoformat())
        if closed is not None:
            etag = etag_for(0, "closed-day", *_scope(game), day.isoformat())
            if etag_matches(if_none_match, etag):
                return Cached(etag=etag, body=None), True
            awards = [_award_from_dict(a) for a in closed.data["awards"]]
            body = DailyAwardsOut(
                date=day.isoformat(), final=True, awards=await self._awards_out(awards)
            )
            return Cached(etag=etag, body=body), True
        cached = await self.boards.cached(
            if_none_match, ("day", *_scope(game), day), lambda: self._live_daily(day, game)
        )
        return cached, False

    async def _live_daily(self, day: date, game: ProgressionGame) -> DailyAwardsOut:
        awards = await self.compute_daily(day, game) if day <= today() else []
        return DailyAwardsOut(
            date=day.isoformat(), final=False, awards=await self._awards_out(awards)
        )

    async def season(
        self, raw_month: str, if_none_match: str | None, game: ProgressionGame = KACHUFUL
    ) -> tuple[Cached[SeasonOut | GameSeasonOut], bool]:
        """`GET /api/seasons/{yyyy-mm}`; the flag is True once the season is closed."""
        month = parse_month(raw_month)
        if game != KACHUFUL:
            return await self._game_season(seasons_of(game), month, if_none_match)
        closed = await achievements_repository.closed(self.session, game, month)
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

    async def seasons(
        self, if_none_match: str | None, game: ProgressionGame = KACHUFUL
    ) -> Cached[list[SeasonListItemOut]]:
        """`GET /api/seasons`: every month since the game's first, newest first."""
        if game != KACHUFUL:
            rules = seasons_of(game)
            return await self.boards.cached(
                if_none_match, ("seasons", game.value), lambda: self._game_seasons_body(rules)
            )
        return await self.boards.cached(if_none_match, ("seasons",), self._seasons_body)

    async def _seasons_body(self) -> list[SeasonListItemOut]:
        first = await boards_repository.first_played_day(self.session)
        if first is None:
            return []
        closed = {
            c.period: c
            for c in await achievements_repository.closed_seasons(self.session, KACHUFUL)
        }
        months = _months_since(first)
        champions: dict[str, str | None] = {}
        for month in months:
            if month in closed:
                found = [a for a in closed[month].data["awards"] if a["code"] == "season_champion"]
                champions[month] = found[0]["playerId"] if found else None
            else:
                board, _ = await self.compute_season(month)
                top = next((r for r in board.rows if r.rank == 1), None)
                champions[month] = None if top is None else top.player_id
        return await self._season_list(months, champions)

    async def _season_list(
        self, months: list[str], champions: dict[str, str | None]
    ) -> list[SeasonListItemOut]:
        views = await self._views({pid for pid in champions.values() if pid})
        return [
            SeasonListItemOut(season=month, champion=_champion(views, champions[month]))
            for month in reversed(months)
        ]

    async def standings(self, rules: WinRateSeasons, month: str) -> list[StandingRow]:
        """A month's win-rate standings (one query)."""
        start, end = month_range_ms(month, settings.tz)
        tallies = await rules.month_tallies(self.session, start, end)
        return win_rate_standings(tallies, rules.min_games)

    async def compute_game_season(
        self, rules: WinRateSeasons, month: str
    ) -> tuple[list[StandingRow], list[Award]]:
        """A non-Kachuful month's standings and awards (with last month's standings)."""
        standings = await self.standings(rules, month)
        last = await self.standings(rules, previous_month(month))
        return standings, await rules.season_awards(self.session, month, standings, last)

    async def _game_season(
        self, rules: WinRateSeasons, month: str, if_none_match: str | None
    ) -> tuple[Cached[GameSeasonOut], bool]:
        closed = await achievements_repository.closed(self.session, rules.game, month)
        if closed is not None:
            etag = etag_for(0, "closed-season", rules.game.value, month)
            if etag_matches(if_none_match, etag):
                return Cached(etag=etag, body=None), True
            standings = [StandingRow(**r) for r in closed.data["standings"]]
            awards = [_award_from_dict(a) for a in closed.data["awards"]]
            body = await self._game_season_out(
                rules, month, (True, closed.data["minGames"]), standings, awards
            )
            return Cached(etag=etag, body=body), True
        cached = await self.boards.cached(
            if_none_match,
            ("season", rules.game.value, month),
            lambda: self._live_game_season(rules, month),
        )
        return cached, False

    async def _live_game_season(self, rules: WinRateSeasons, month: str) -> GameSeasonOut:
        standings, awards = await self.compute_game_season(rules, month)
        return await self._game_season_out(
            rules, month, (False, rules.min_games), standings, awards
        )

    async def _game_season_out(
        self,
        rules: WinRateSeasons,
        month: str,
        state: tuple[bool, int],
        standings: list[StandingRow],
        awards: list[Award],
    ) -> GameSeasonOut:
        """`state`: (final, minGames) - a closed season keeps the minimum it closed with."""
        final, min_games = state
        views = await self._views({r.player_id for r in standings} | {a.player_id for a in awards})
        return GameSeasonOut(
            season=month,
            final=final,
            game_kind=rules.game,
            min_games=min_games,
            standings=[_standing_out(views[r.player_id], r) for r in standings],
            awards=[_award_out(views[a.player_id], a) for a in awards],
        )

    async def _game_seasons_body(self, rules: WinRateSeasons) -> list[SeasonListItemOut]:
        first = await rules.first_played(self.session)
        if first is None:
            return []
        closed = {
            c.period: c
            for c in await achievements_repository.closed_seasons(self.session, rules.game)
        }
        months = _months_since(first)
        champions: dict[str, str | None] = {}
        for month in months:
            if month in closed:
                rows = [StandingRow(**r) for r in closed[month].data["standings"]]
            else:
                rows = await self.standings(rules, month)
            champions[month] = _top(rows)
        return await self._season_list(months, champions)

    async def close_day(self, day: date, game: ProgressionGame = KACHUFUL) -> list[Award] | None:
        """Freeze a past day's awards for one game (None when already closed). No commit."""
        period = day.isoformat()
        if await achievements_repository.closed(self.session, game, period) is not None:
            return None
        awards = await self.compute_daily(day, game)
        _, end = day_range_ms(day, settings.tz)
        await self._record(awards, period, end - 1, game)
        await achievements_repository.close(
            self.session, game, period, "day", {"awards": [_award_dict(a) for a in awards]}
        )
        await app_state_repository.bump(self.session)
        return awards

    async def close_season(
        self, month: str, game: ProgressionGame = KACHUFUL
    ) -> list[Award] | None:
        """Freeze a past month's standings and awards for one game (None when already closed).

        Does not commit.
        """
        if await achievements_repository.closed(self.session, game, month) is not None:
            return None
        if game != KACHUFUL:
            return await self._close_game_season(seasons_of(game), month)
        board, awards = await self.compute_season(month)
        await self._season_closed(awards, month, game)
        await achievements_repository.close(
            self.session,
            game,
            month,
            "season",
            {
                "standings": [_board_row_dict(r) for r in board.rows],
                "awards": [_award_dict(a) for a in awards],
            },
        )
        await app_state_repository.bump(self.session)
        return awards

    async def _close_game_season(self, rules: WinRateSeasons, month: str) -> list[Award]:
        standings, awards = await self.compute_game_season(rules, month)
        # A month nobody played this game posts no feed item (it would only be noise).
        await self._season_closed(awards, month, rules.game, post=bool(standings))
        data = {
            "minGames": rules.min_games,
            "standings": [asdict(r) for r in standings],
            "awards": [_award_dict(a) for a in awards],
        }
        await achievements_repository.close(self.session, rules.game, month, "season", data)
        await app_state_repository.bump(self.session)
        return awards

    async def _season_closed(
        self, awards: list[Award], month: str, game: ProgressionGame, *, post: bool = True
    ) -> None:
        """Store a closing season's awards and (with `post`) its feed item."""
        _, end = month_range_ms(month, settings.tz)
        await self._record(awards, month, end - 1, game)
        if not post:
            return
        champions = [a.player_id for a in awards if a.code == "season_champion"]
        await achievements_repository.add_activity(
            self.session,
            [
                {
                    "game": game.value,
                    "kind": "season_closed",
                    "data": {"season": month, "champions": champions},
                }
            ],
        )

    async def _record(
        self, awards: list[Award], period: str, earned_at: int, game: ProgressionGame
    ) -> None:
        rows = await achievements_repository.insert(
            self.session, [award_earned(a, period, earned_at) for a in awards], game=game
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
