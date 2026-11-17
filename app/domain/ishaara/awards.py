"""Ishaara daily awards and seasons (app: src/games/ishaara/domain/awards.ts), over finished games'
player rows. Seasons rank players by win rate (`domain.progression.win_rate_standings`, at least
SEASON_MIN_GAMES finished games in the month). Ties share an award.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.domain.progression import (
    Award,
    StandingRow,
    best_of,
    climber_awards,
    iron_awards,
    podium_awards,
)

SEASON_MIN_GAMES = 8
DAY_GIVER_MIN_CLUES = 4
SEASON_GIVER_MIN_CLUES = 20
DAY_STAR_MIN_WINS = 2
DAILY_CODES: frozenset[str] = frozenset(
    {"day_star", "day_giver", "day_speed", "day_trapped", "day_grinder"}
)
SEASON_CODES: frozenset[str] = frozenset(
    {
        "season_champion",
        "season_runner_up",
        "season_third",
        "season_giver",
        "season_iron",
        "season_climber",
    }
)
CLUE_GIVER = "clue-giver"
GUESSER = "guesser"


@dataclass(frozen=True)
class IshPlayRow:
    """One seat of a finished (not abandoned) game: an ishaara_game_players row."""

    game_id: str
    player_id: str
    team: str
    role: str
    won: bool
    words_found: int
    clues_given: int
    # The team hit the trap (every seat of the team has it).
    trap_hit: bool
    finished_at: int


@dataclass(frozen=True)
class IshDayGame:
    """One finished game of the day: how it ended and when its first clue was given (ms)."""

    game_id: str
    ended_by: str
    started_at: int | None
    finished_at: int


@dataclass(frozen=True)
class IshDayFacts:
    """One APP_TZ day [start_ms, end_ms) of play, as daily awards read it."""

    start_ms: int
    end_ms: int
    rows: tuple[IshPlayRow, ...] = ()
    games: tuple[IshDayGame, ...] = ()


@dataclass(frozen=True)
class IshMonthFacts:
    """One APP_TZ month [start_ms, end_ms) of play, as the season's own awards read it."""

    start_ms: int
    end_ms: int
    rows: tuple[IshPlayRow, ...] = ()


def _tally(rows: Iterable[IshPlayRow], value: Callable[[IshPlayRow], int]) -> list[tuple[str, int]]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[r.player_id] += value(r)
    return list(by.items())


def _per_clue(rows: Iterable[IshPlayRow], min_clues: int) -> list[tuple[str, float]]:
    """Cards found per clue as clue-giver, for those who gave at least `min_clues` clues."""
    found: dict[str, int] = defaultdict(int)
    clues: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.role == CLUE_GIVER:
            found[r.player_id] += r.words_found
            clues[r.player_id] += r.clues_given
    return [(pid, found[pid] / n) for pid, n in clues.items() if n >= min_clues]


def _quickest(rows: Iterable[IshPlayRow], games: Iterable[IshDayGame]) -> list[tuple[str, int]]:
    """Each winner's quickest all-found win, first clue to last card, in ms."""
    took = {
        g.game_id: g.finished_at - g.started_at
        for g in games
        if g.ended_by == "all-found" and g.started_at is not None
    }
    by: dict[str, int] = {}
    for r in rows:
        ms = took.get(r.game_id)
        if r.won and ms is not None:
            by[r.player_id] = min(by.get(r.player_id, ms), ms)
    return list(by.items())


def _won(r: IshPlayRow) -> int:
    return 1 if r.won else 0


def _trapped(r: IshPlayRow) -> int:
    return 1 if r.role == GUESSER and r.trap_hit else 0


def _one(_: IshPlayRow) -> int:
    return 1


def daily_awards(facts: IshDayFacts) -> list[Award]:
    """The day's awards from the rows and games that finished that day."""
    rows = facts.rows
    return [
        *best_of(_tally(rows, _won), "day_star", ok=lambda v: v >= DAY_STAR_MIN_WINS),
        *best_of(_per_clue(rows, DAY_GIVER_MIN_CLUES), "day_giver", ok=lambda v: v > 0),
        *best_of(_quickest(rows, facts.games), "day_speed", lowest=True),
        *best_of(_tally(rows, _trapped), "day_trapped", ok=lambda v: v >= 1),
        *best_of(_tally(rows, _one), "day_grinder"),
    ]


def season_awards(
    standings: Sequence[StandingRow], last: Sequence[StandingRow], facts: IshMonthFacts
) -> list[Award]:
    """The podium, the clue-giver of the month, the iron player and the climber."""
    giver = _per_clue(facts.rows, SEASON_GIVER_MIN_CLUES)
    return [
        *podium_awards(standings),
        *best_of(giver, "season_giver", ok=lambda v: v > 0),
        *iron_awards(standings),
        *climber_awards(standings, last),
    ]
