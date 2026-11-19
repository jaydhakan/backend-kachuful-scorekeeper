"""Spades daily awards and seasons (plan §3; app: src/games/spades/domain/awards.ts).

A day or month counts the games that finished in it, as the boards do. Seasons rank players by win
rate (`domain.progression.win_rate_standings`, at least SEASON_MIN_GAMES finished games in the
month). Ties share an award; nothing is awarded when nobody passes the minimum.
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
from app.domain.spades.achievements import SpRow

SEASON_MIN_GAMES = 10
DAY_SHARP_BIDS = 5
SEASON_SHARP_BIDS = 40
SEASON_PAIR_GAMES = 6
# The moments daily awards count, per player, in the day's finished games.
DAY_MOMENT_CODES = ("set_them", "boston")

DAILY_CODES: frozenset[str] = frozenset(
    {"day_pair", "day_sharp", "day_nil", "day_setter", "day_boston", "day_bags", "day_grinder"}
)
SEASON_CODES: frozenset[str] = frozenset(
    {
        "season_champion",
        "season_runner_up",
        "season_third",
        "season_sharpest",
        "season_nil",
        "season_pair",
        "season_iron",
        "season_climber",
    }
)


@dataclass(frozen=True)
class SpDayFacts:
    """One APP_TZ day [start_ms, end_ms): rows of the games that finished in it, and their moments.

    `moments` holds one (player_id, code) per set_them or boston moment of those games.
    """

    start_ms: int
    end_ms: int
    rows: tuple[SpRow, ...] = ()
    moments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SpMonthFacts:
    """One APP_TZ month [start_ms, end_ms): rows of the games that finished in it."""

    start_ms: int
    end_ms: int
    rows: tuple[SpRow, ...] = ()


def _sums(rows: Iterable[SpRow], of: Callable[[SpRow], int]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[r.player_id] += of(r)
    return dict(out)


def _made_rates(rows: Sequence[SpRow], min_bids: int) -> list[tuple[str, float]]:
    bids = _sums(rows, lambda r: r.bids)
    made = _sums(rows, lambda r: r.bids_made)
    return [(p, made[p] / n) for p, n in bids.items() if n >= min_bids]


def _pairs(rows: Iterable[SpRow]) -> dict[tuple[str, str], tuple[int, int]]:
    """Games and wins of each pair of partners seated together at the end, keyed both ways round."""
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for r in rows:
        if r.result is None or r.partner_id is None:
            continue
        games, wins = out.get((r.player_id, r.partner_id), (0, 0))
        out[(r.player_id, r.partner_id)] = (games + 1, wins + (r.result == "win"))
    return out


def _best_pair(
    rows: Iterable[SpRow], value: Callable[[int, int], float | None]
) -> list[tuple[str, float]]:
    """Each player's best value over their pairs: a top pair's two players are then the top."""
    out: dict[str, float] = {}
    for (player, _), (games, wins) in _pairs(rows).items():
        v = value(games, wins)
        if v is not None:
            out[player] = max(out.get(player, v), v)
    return list(out.items())


def _moment_counts(facts: SpDayFacts, code: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for player, c in facts.moments:
        if c == code:
            out[player] += 1
    return dict(out)


def _nils(r: SpRow) -> int:
    return r.nils_made


def daily_awards(facts: SpDayFacts) -> list[Award]:
    """The day's awards, in catalogue order."""
    rows = facts.rows
    bostons = sorted(_moment_counts(facts, "boston").items())
    return [
        *best_of(_best_pair(rows, lambda _, wins: wins), "day_pair", ok=lambda v: v >= 2),
        *best_of(_made_rates(rows, DAY_SHARP_BIDS), "day_sharp"),
        *best_of(_sums(rows, _nils).items(), "day_nil", ok=lambda v: v >= 1),
        *best_of(_moment_counts(facts, "set_them").items(), "day_setter", ok=lambda v: v >= 2),
        *(Award("day_boston", player, count) for player, count in bostons),
        *best_of(_sums(rows, lambda r: r.bags).items(), "day_bags", ok=lambda v: v >= 5),
        *best_of(_sums(rows, lambda r: r.hands).items(), "day_grinder", ok=lambda v: v > 0),
    ]


def _pair_rate(games: int, wins: int) -> float | None:
    return wins / games if games >= SEASON_PAIR_GAMES else None


def season_awards(
    standings: Sequence[StandingRow], last: Sequence[StandingRow], facts: SpMonthFacts
) -> list[Award]:
    """The month's awards from its standings, last month's standings and its rows."""
    rows = facts.rows
    return [
        *podium_awards(standings),
        *best_of(_made_rates(rows, SEASON_SHARP_BIDS), "season_sharpest"),
        *best_of(_sums(rows, _nils).items(), "season_nil", ok=lambda v: v >= 1),
        *best_of(_best_pair(rows, _pair_rate), "season_pair", ok=lambda v: v > 0),
        *iron_awards(standings),
        *climber_awards(standings, last),
    ]
