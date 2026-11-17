"""`GameRules`: a game's facts loaders wired to its pure rules, as one `GameProgression`.

Spades, Kaali Teeri and Ishaara are each one `GameRules` in `registry.py`; their loaders live in
`repositories/<game>_progression.py` and their rules in `domain/<game>/achievements.py` and
`awards.py`. A loader returns the game's own facts type; only its rules read it.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProgressionGame
from app.domain.progression import Award, Earned, StandingRow, WinTally
from app.domain.time import day_of, day_range_ms, month_range_ms, to_ms
from app.settings import settings

type Window[T] = Callable[[AsyncSession, int, int], Awaitable[T]]


@dataclass(frozen=True)
class GameRules[GameF, CareerF, DayF, MonthF]:
    """One game's codes, loaders and rules (a `GameProgression` and `WinRateSeasons`)."""

    game: ProgressionGame
    game_codes: frozenset[str]
    career_codes: frozenset[str]
    ladder_ids: tuple[str, ...]
    min_games: int
    load_game: Callable[[AsyncSession, uuid.UUID], Awaitable[GameF | None]]
    load_career: Callable[[AsyncSession, set[uuid.UUID]], Awaitable[CareerF]]
    load_day: Window[DayF]
    load_month: Window[MonthF]
    load_tallies: Window[list[WinTally]]
    load_first: Callable[[AsyncSession], Awaitable[datetime | None]]
    earn_game: Callable[[GameF, ZoneInfo], list[Earned]]
    earn_career: Callable[[CareerF], list[Earned]]
    count_ladders: Callable[[CareerF, str], dict[str, int]]
    award_day: Callable[[DayF], list[Award]]
    award_season: Callable[[Sequence[StandingRow], Sequence[StandingRow], MonthF], list[Award]]

    async def game_earned(self, session: AsyncSession, game_id: uuid.UUID) -> list[Earned]:
        """Everything one stored game earns now."""
        facts = await self.load_game(session, game_id)
        return [] if facts is None else self.earn_game(facts, settings.tz)

    async def career_earned(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> list[Earned]:
        """Every career row these players hold now."""
        return self.earn_career(await self.load_career(session, player_ids))

    async def ladder_counts(self, session: AsyncSession, player_id: uuid.UUID) -> dict[str, int]:
        """Each ladder's count for one player."""
        facts = await self.load_career(session, {player_id})
        return self.count_ladders(facts, str(player_id))

    async def daily_awards(self, session: AsyncSession, day: date) -> list[Award]:
        """One APP_TZ day's awards."""
        start, end = day_range_ms(day, settings.tz)
        return self.award_day(await self.load_day(session, start, end))

    async def month_tallies(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> list[WinTally]:
        """Finished games and wins per player in [start, end)."""
        return await self.load_tallies(session, start_ms, end_ms)

    async def season_awards(
        self,
        session: AsyncSession,
        month: str,
        standings: list[StandingRow],
        last: list[StandingRow],
    ) -> list[Award]:
        """A month's awards."""
        start, end = month_range_ms(month, settings.tz)
        return self.award_season(standings, last, await self.load_month(session, start, end))

    async def first_played(self, session: AsyncSession) -> date | None:
        """The APP_TZ day of the first finished game, if any."""
        first = await self.load_first(session)
        return None if first is None else day_of(to_ms(first), settings.tz)
