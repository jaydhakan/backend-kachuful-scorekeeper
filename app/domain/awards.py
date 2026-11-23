"""Daily awards and season awards (plan §8.4-8.5; app: src/domain/awards.ts)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain.leaderboard import Board, Metric, Tally, accuracy_of
from app.domain.progression import Award, best_of

SEASON_MIN_ROUNDS = 50
# Kachuful's month (medals, the season title) ranks by bid accuracy, ties on the average.
SEASON_METRIC = Metric.ACCURACY


class ScoredRound(Protocol):
    """What daily awards read from a round (RoundFact and stored rows both fit)."""

    @property
    def player_id(self) -> str: ...
    @property
    def game_id(self) -> str: ...
    @property
    def round_index(self) -> int: ...
    @property
    def done_at(self) -> int: ...
    @property
    def points(self) -> int: ...


@dataclass(frozen=True)
class DayRound:
    """A stored round, as daily awards need it."""

    player_id: str
    game_id: str
    round_index: int
    done_at: int
    points: int


def _best_round(rounds: Sequence[ScoredRound], code: str, *, lowest: bool) -> list[Award]:
    """The single best round; the earliest (then player id) wins a tie."""
    winner: ScoredRound | None = None
    for r in sorted(rounds, key=lambda r: (r.done_at, r.player_id)):
        if winner is None or (r.points < winner.points if lowest else r.points > winner.points):
            winner = r
    if winner is None:
        return []
    return [Award(code, winner.player_id, winner.points, winner.game_id, winner.round_index)]


def daily_awards(rounds: Sequence[ScoredRound], tallies: Iterable[Tally]) -> list[Award]:
    """§8.4 from the day's rounds and each player's tally for the day."""
    if not rounds:
        return []
    t = [x for x in tallies if x.rounds > 0]
    return [
        *best_of(((x.player_id, x.points) for x in t), "day_star", ok=lambda v: v > 0),
        *[a for a in _best_round(rounds, "day_bang", lowest=False) if a.value > 0],
        *[a for a in _best_round(rounds, "day_ouch", lowest=True) if a.value < 0],
        *best_of(((x.player_id, x.longest_hot) for x in t), "day_hot", ok=lambda v: v >= 3),
        *best_of(((x.player_id, x.longest_cold) for x in t), "day_ice", ok=lambda v: v >= 3),
        *best_of(((x.player_id, accuracy_of(x)) for x in t if x.rounds >= 10), "day_sharp"),
        *best_of(((x.player_id, x.rounds) for x in t), "day_grinder"),
        *best_of(((x.player_id, x.hook_hits) for x in t), "day_hooked", ok=lambda v: v >= 1),
    ]


def season_awards(standings: Board, last_month: Board) -> list[Award]:
    """§8.5 from a month's standings (more than 50 rounds) and last month's.

    No `season_sharpest` since 2026-11-23: on accuracy it always went to the champion. Its
    definition and Sikke rate stay so the ones already given still read and pay.
    """
    qualified = [r for r in standings.rows if r.qualified]
    awards: list[Award] = []
    for rank, code in ((1, "season_champion"), (2, "season_runner_up"), (3, "season_third")):
        awards += [Award(code, r.player_id, r.value) for r in qualified if r.rank == rank]
    awards += best_of(((r.player_id, r.games) for r in qualified), "season_iron")
    before = {r.player_id: r.rank for r in last_month.rows if r.qualified and r.rank is not None}
    awards += best_of(
        (
            (r.player_id, before[r.player_id] - (r.rank or 0))
            for r in qualified
            if r.player_id in before
        ),
        "season_climber",
        ok=lambda v: v > 0,
    )
    return awards
