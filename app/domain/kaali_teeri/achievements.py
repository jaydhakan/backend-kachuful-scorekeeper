"""Kaali Teeri achievements (plan §4; app: src/games/kaali-teeri/domain/achievements.ts). STUB until P4: earns nothing.

P4 fills this module: the catalogue (10 hand moments, 11 game, 23 career), the facts the rules read and
the rules themselves, with golden fixtures. Keep the names and signatures below: the engine
(`services/progression/registry.py`) already calls them after every upload, finish and delete.
Rules return `Earned` rows: moment and game rows carry `game_id` (and `round_index` = the hand
index for moments), career rows carry the `game_id` of the game that earned them; no `period`.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.domain.progression import Earned

# Hand moments and game-end codes: the rows one stored game owns.
MOMENT_CODES: frozenset[str] = frozenset()
GAME_CODES: frozenset[str] = frozenset()
# Career codes: recomputed per player over every finished game.
CAREER_CODES: frozenset[str] = frozenset()
# The ladders `ladder_counts` returns (ids match the app's), in the app's order.
LADDER_IDS: tuple[str, ...] = ("wins", "games", "hands", "made", "partner", "streak", "crew")


@dataclass(frozen=True)
class KtGameFacts:
    """One stored game of Kaali Teeri, as hand and game achievements read it."""

    game_id: str


@dataclass(frozen=True)
class KtCareerFacts:
    """Some players' finished games, as career achievements and ladders read them."""

    player_ids: tuple[str, ...]


def game_achievements(facts: KtGameFacts, tz: ZoneInfo) -> list[Earned]:
    """Hand moments and game-end achievements of one game; "night" is judged in `tz`."""
    return []


def career_achievements(facts: KtCareerFacts) -> list[Earned]:
    """Every career achievement the players in `facts` hold."""
    return []


def ladder_counts(facts: KtCareerFacts, player_id: str) -> dict[str, int]:
    """Each ladder's count for one player, counted the way its codes are earned."""
    return dict.fromkeys(LADDER_IDS, 0)
