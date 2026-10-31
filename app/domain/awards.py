"""Daily awards and season awards (plan §8.4-8.5; app: src/domain/awards.ts)."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.domain.derive import RoundFact
from app.domain.leaderboard import Board, Tally, accuracy_of

SEASON_MIN_ROUNDS = 50


@dataclass(frozen=True)
class Award:
    """One daily or season award."""

    code: str
    player_id: str
    value: float
    game_id: str | None = None
    round_index: int | None = None


def _best(
    values: Iterable[tuple[str, float]],
    code: str,
    *,
    lowest: bool = False,
    ok: Callable[[float], bool] = lambda _: True,
) -> list[Award]:
    """Everyone tied on the best value wins; nothing when no value passes `ok`."""
    good = [(pid, v) for pid, v in values if ok(v)]
    if not good:
        return []
    top = min(v for _, v in good) if lowest else max(v for _, v in good)
    return sorted((Award(code, pid, top) for pid, v in good if v == top), key=lambda a: a.player_id)


def _best_round(rounds: list[RoundFact], code: str, *, lowest: bool) -> list[Award]:
    """The single best round; the earliest (then player id) wins a tie."""
    winner: RoundFact | None = None
    for r in sorted(rounds, key=lambda r: (r.done_at, r.player_id)):
        if winner is None or (r.points < winner.points if lowest else r.points > winner.points):
            winner = r
    if winner is None:
        return []
    return [Award(code, winner.player_id, winner.points, winner.game_id, winner.round_index)]


def daily_awards(rounds: list[RoundFact], tallies: Iterable[Tally]) -> list[Award]:
    """§8.4 from the day's rounds and each player's tally for the day."""
    if not rounds:
        return []
    t = [x for x in tallies if x.rounds > 0]
    return [
        *_best(((x.player_id, x.points) for x in t), "day_star", ok=lambda v: v > 0),
        *[a for a in _best_round(rounds, "day_bang", lowest=False) if a.value > 0],
        *[a for a in _best_round(rounds, "day_ouch", lowest=True) if a.value < 0],
        *_best(((x.player_id, x.longest_hot) for x in t), "day_hot", ok=lambda v: v >= 3),
        *_best(((x.player_id, x.longest_cold) for x in t), "day_ice", ok=lambda v: v >= 3),
        *_best(((x.player_id, accuracy_of(x)) for x in t if x.rounds >= 10), "day_sharp"),
        *_best(((x.player_id, x.rounds) for x in t), "day_grinder"),
        *_best(((x.player_id, x.hook_hits) for x in t), "day_hooked", ok=lambda v: v >= 1),
    ]


def season_awards(standings: Board, last_month: Board) -> list[Award]:
    """§8.5 from a month's standings (more than 50 rounds) and last month's."""
    qualified = [r for r in standings.rows if r.qualified]
    awards: list[Award] = []
    for rank, code in ((1, "season_champion"), (2, "season_runner_up"), (3, "season_third")):
        awards += [Award(code, r.player_id, r.value) for r in qualified if r.rank == rank]
    awards += _best(
        ((r.player_id, r.accuracy) for r in qualified if r.rounds >= 30), "season_sharpest"
    )
    awards += _best(((r.player_id, r.games) for r in qualified), "season_iron")
    before = {r.player_id: r.rank for r in last_month.rows if r.qualified and r.rank is not None}
    awards += _best(
        (
            (r.player_id, before[r.player_id] - (r.rank or 0))
            for r in qualified
            if r.player_id in before
        ),
        "season_climber",
        ok=lambda v: v > 0,
    )
    return awards
