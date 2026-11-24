"""Group milestones and firsts (rewards finish §6): app-wide, derived from finished games.

A group milestone is the nth finished, non-void game of every kind (`group:all:<n>`) or of one
game (`group:<game>:<n>`); a first is the earliest finished game that qualifies (`first:...`).
Pure: the service reads the ranked games and the firsts' facts, and reconciles the table.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import ProgressionGame

GROUP_ALL_MARKS: tuple[int, ...] = (100, 250, 500, 1000, 2500, 5000)
"""Finished games of every kind that make a milestone."""
GROUP_GAME_MARKS: tuple[int, ...] = (50, 100, 250, 500, 1000)
"""Finished games of one game (both Spades kinds count as Spades) that make a milestone."""
TEN_PLAYERS = 10
"""A Kachuful table this big is a first."""
TEN_PLAYERS_KEY = "first:kachuful:ten_players"

# Firsts read from achievement rows: the key, then the game and code that earn it.
FIRST_CODES: dict[str, tuple[ProgressionGame, str]] = {
    "first:kachuful:perfect_game": (ProgressionGame.KACHUFUL, "perfect_game"),
    "first:spades:boston": (ProgressionGame.SPADES, "boston"),
    # A Blind Nil made is the `blindside` moment.
    "first:spades:blind_nil": (ProgressionGame.SPADES, "blindside"),
    "first:kaali-teeri:thousand": (ProgressionGame.KAALI_TEERI, "thousandaire"),
    "first:ishaara:speedrun": (ProgressionGame.ISHAARA, "speedrun"),
}


class MilestoneKind(StrEnum):
    """`milestones.kind` is checked against these values."""

    GROUP = "group"
    FIRST = "first"


@dataclass(frozen=True)
class RankedGame:
    """A finished game at a mark: its place among every kind's games and among its own game's."""

    game_id: str
    game: ProgressionGame
    ended_at: int
    players: tuple[str, ...]
    n_all: int
    n_game: int


@dataclass(frozen=True)
class FirstFact:
    """The earliest finished game that qualifies for a first, and who earned it there."""

    key: str
    game: ProgressionGame
    game_id: str
    ended_at: int
    players: tuple[str, ...]


@dataclass(frozen=True)
class Milestone:
    """One milestone as stored; `n` only for group ones, `game` null for `group:all`.

    `played` is the game `game_id` belongs to (its feed item is filed under it).
    """

    key: str
    kind: MilestoneKind
    game: ProgressionGame | None
    game_id: str
    players: tuple[str, ...]
    at: int
    played: ProgressionGame
    n: int | None = None


def group_milestones(games: Iterable[RankedGame]) -> list[Milestone]:
    """Every group milestone the ranked games reach."""
    out: list[Milestone] = []
    for g in games:
        players = tuple(sorted(g.players))
        if g.n_all in GROUP_ALL_MARKS:
            out.append(
                Milestone(
                    f"group:all:{g.n_all}",
                    MilestoneKind.GROUP,
                    None,
                    g.game_id,
                    players,
                    g.ended_at,
                    g.game,
                    g.n_all,
                )
            )
        if g.n_game in GROUP_GAME_MARKS:
            out.append(
                Milestone(
                    f"group:{g.game}:{g.n_game}",
                    MilestoneKind.GROUP,
                    g.game,
                    g.game_id,
                    players,
                    g.ended_at,
                    g.game,
                    g.n_game,
                )
            )
    return out


def first_milestones(facts: Iterable[FirstFact]) -> list[Milestone]:
    """Firsts: the earliest qualifying game per key (ties on time break on the game id)."""
    best: dict[str, FirstFact] = {}
    for f in facts:
        have = best.get(f.key)
        if have is None or (f.ended_at, f.game_id) < (have.ended_at, have.game_id):
            best[f.key] = f
    return [
        Milestone(
            f.key,
            MilestoneKind.FIRST,
            f.game,
            f.game_id,
            tuple(sorted(f.players)),
            f.ended_at,
            f.game,
        )
        for f in best.values()
    ]


@dataclass(frozen=True)
class MilestoneDiff:
    """What a recompute writes: `created` are new keys or keys that moved to another game."""

    upsert: list[Milestone]
    created: list[Milestone]
    gone: list[str]


def milestone_diff(stored: Mapping[str, Milestone], desired: Iterable[Milestone]) -> MilestoneDiff:
    """Compare desired milestones with stored ones, in key order."""
    wanted = {m.key: m for m in desired}
    upsert = [m for k, m in sorted(wanted.items()) if stored.get(k) != m]
    created = [m for m in upsert if m.key not in stored or stored[m.key].game_id != m.game_id]
    gone = sorted(k for k in stored if k not in wanted)
    return MilestoneDiff(upsert=upsert, created=created, gone=gone)
