"""Ishaara achievements (plan §5; app: src/games/ishaara/domain/achievements.ts). STUB until P5: earns nothing.

P5 fills this module: the catalogue (5 turn moments, 11 game, 19 career), the facts the rules read and
the rules themselves, with golden fixtures. Keep the names and signatures below: the engine
(`services/progression/registry.py`) already calls them after every upload, finish and delete.
Rules return `Earned` rows: moment and game rows carry `game_id` (and `round_index` = the turn
index for moments), career rows carry the `game_id` of the game that earned them; no `period`.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.domain.progression import Earned

# Turn moments and game-end codes: the rows one stored game owns.
MOMENT_CODES: frozenset[str] = frozenset()
GAME_CODES: frozenset[str] = frozenset()
# Career codes: recomputed per player over every finished game.
CAREER_CODES: frozenset[str] = frozenset()
# The ladders `ladder_counts` returns (ids match the app's), in the app's order.
LADDER_IDS: tuple[str, ...] = ("wins", "games", "giver_wins", "clues", "found", "crew")


@dataclass(frozen=True)
class IshGameFacts:
    """One stored game of finished (not abandoned) Ishaara, as turn and game achievements read it."""

    game_id: str


@dataclass(frozen=True)
class IshCareerFacts:
    """Some players' finished games, as career achievements and ladders read them."""

    player_ids: tuple[str, ...]


def game_achievements(facts: IshGameFacts, tz: ZoneInfo) -> list[Earned]:
    """Turn moments and game-end achievements of one game; "night" is judged in `tz`."""
    return []


def career_achievements(facts: IshCareerFacts) -> list[Earned]:
    """Every career achievement the players in `facts` hold."""
    return []


def ladder_counts(facts: IshCareerFacts, player_id: str) -> dict[str, int]:
    """Each ladder's count for one player, counted the way its codes are earned."""
    return dict.fromkeys(LADDER_IDS, 0)
