"""What a game plugs into the achievements engine, the awards and the jobs.

One class per game (`kachuful.py`, `spades.py`, `kaali_teeri.py`, `ishaara.py`) wires its
facts loader (`repositories/<game>_progression.py`) to its rules (`domain/<game>/achievements.py`
and `awards.py`); `registry.py` lists them. The engine keeps the achievements table equal to what
the rules return: it inserts new keys, deletes keys that no longer hold and never touches a kept row.
"""

import uuid
from datetime import date
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProgressionGame
from app.domain.progression import Award, Earned, StandingRow, WinTally


class GameProgression(Protocol):
    """One game's achievements, ladders and daily awards."""

    @property
    def game(self) -> ProgressionGame:
        """The game its rows are stored under."""
        ...

    @property
    def game_codes(self) -> frozenset[str]:
        """Moment and game codes: the rows a stored game owns (`game_id` set, no period)."""
        ...

    @property
    def career_codes(self) -> frozenset[str]:
        """Career codes: recomputed per player from everything stored."""
        ...

    @property
    def ladder_ids(self) -> tuple[str, ...]:
        """The ladders `ladder_counts` returns, in the app's order."""
        ...

    async def game_earned(self, session: AsyncSession, game_id: uuid.UUID) -> list[Earned]:
        """Everything one stored game earns now (moments and game codes only)."""
        ...

    async def career_earned(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> list[Earned]:
        """Every career row these players hold now (career codes only)."""
        ...

    async def ladder_counts(self, session: AsyncSession, player_id: uuid.UUID) -> dict[str, int]:
        """Each ladder's count for one player, counted the way its codes are earned."""
        ...

    async def daily_awards(self, session: AsyncSession, day: date) -> list[Award]:
        """One APP_TZ day's awards."""
        ...


class WinRateSeasons(Protocol):
    """A game whose months rank players by win rate (every game but Kachuful)."""

    @property
    def game(self) -> ProgressionGame:
        """The game its seasons are stored under."""
        ...

    @property
    def min_games(self) -> int:
        """Finished games in the month a player needs to qualify."""
        ...

    async def month_tallies(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> list[WinTally]:
        """Finished games and wins per player in [start, end)."""
        ...

    async def season_awards(
        self,
        session: AsyncSession,
        month: str,
        standings: list[StandingRow],
        last: list[StandingRow],
    ) -> list[Award]:
        """A month's awards from its standings, last month's and the game's own facts."""
        ...

    async def first_played(self, session: AsyncSession) -> date | None:
        """The APP_TZ day of the first finished game, if any."""
        ...
