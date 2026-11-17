"""Kaali Teeri daily awards and seasons (plan §4; app: src/games/kaali-teeri/domain/awards.ts). STUB until P4.

P4 fills this module (7 daily, 7 season codes) with golden fixtures. Seasons rank players by win
rate (`domain.progression.win_rate_standings`, at least SEASON_MIN_GAMES finished games in the
month); `season_awards` gets those standings, last month's and this game's month facts, and can
build on `podium_awards`, `iron_awards` and `climber_awards`. Ties share an award.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.progression import Award, StandingRow

SEASON_MIN_GAMES = 8
DAILY_CODES: frozenset[str] = frozenset()
SEASON_CODES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class KtDayFacts:
    """One APP_TZ day [start_ms, end_ms) of play, as daily awards read it."""

    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class KtMonthFacts:
    """One APP_TZ month [start_ms, end_ms) of play, as the season's own awards read it."""

    start_ms: int
    end_ms: int


def daily_awards(facts: KtDayFacts) -> list[Award]:
    """The day's awards."""
    return []


def season_awards(
    standings: Sequence[StandingRow], last: Sequence[StandingRow], facts: KtMonthFacts
) -> list[Award]:
    """The month's awards from its standings, last month's standings and its facts."""
    return []
