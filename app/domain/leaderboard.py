"""Leaderboard tallies and ranking (plan §2, §6.5; app: src/domain/leaderboard.ts, ranges.ts)."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from app.domain.derive import GameFact, RoundFact

DAY_MS = 86_400_000
_SLACK_MS = 3_600_000  # a day or week may be an hour longer across a clock change


class Metric(StrEnum):
    """What a board ranks by."""

    AVG_PER_ROUND = "avgPerRound"
    POINTS = "points"
    WINS = "wins"
    WIN_RATE = "winRate"
    ACCURACY = "accuracy"
    BEST_ROUND = "bestRound"
    LONGEST_HOT = "longestHot"
    GAMES = "games"
    ROUNDS = "rounds"


RATE_METRICS = frozenset({Metric.AVG_PER_ROUND, Metric.ACCURACY, Metric.WIN_RATE})


@dataclass(frozen=True)
class Tally:
    """What one player did in a range."""

    player_id: str
    points: int = 0
    rounds: int = 0
    made: int = 0
    games: int = 0
    games_ended: int = 0
    wins: int = 0
    best_round: float = -math.inf
    worst_round: float = math.inf
    longest_hot: int = 0
    longest_cold: int = 0
    hook_hits: int = 0


def merge_tallies(a: Tally, b: Tally) -> Tally:
    """Two parts of the same player's range (e.g. whole days plus edge hours)."""
    return replace(
        a,
        points=a.points + b.points,
        rounds=a.rounds + b.rounds,
        made=a.made + b.made,
        games=a.games + b.games,
        games_ended=a.games_ended + b.games_ended,
        wins=a.wins + b.wins,
        best_round=max(a.best_round, b.best_round),
        worst_round=min(a.worst_round, b.worst_round),
        longest_hot=max(a.longest_hot, b.longest_hot),
        longest_cold=max(a.longest_cold, b.longest_cold),
        hook_hits=a.hook_hits + b.hook_hits,
    )


@dataclass(frozen=True)
class BoardRow:
    """One player on a board. `value` is exact; the app rounds it for display."""

    rank: int | None
    qualified: bool
    player_id: str
    value: float
    points: int
    games: int
    rounds: int
    wins: int
    accuracy: float
    longest_hot: int
    rank_delta: int | None = None


@dataclass(frozen=True)
class Board:
    """Ranked rows and the minimum rounds used."""

    rows: list[BoardRow]
    min_rounds: int


def min_rounds_for(from_ms: int, to_ms: int) -> int:
    """More than this many rounds earns a rank on a rate board: 10 a day, 25 a week, else 50."""
    length = to_ms - from_ms
    if length <= DAY_MS + _SLACK_MS:
        return 10
    if length <= 7 * DAY_MS + _SLACK_MS:
        return 25
    return 50


def previous_window(from_ms: int, to_ms: int) -> tuple[int, int]:
    """The window of the same length just before (for rank movement)."""
    return max(0, from_ms - (to_ms - from_ms)), from_ms


def accuracy_of(t: Tally) -> float:
    """Made ÷ rounds."""
    return t.made / t.rounds if t.rounds else 0


def avg_of(t: Tally) -> float:
    """Points ÷ rounds."""
    return t.points / t.rounds if t.rounds else 0


def win_rate_of(t: Tally) -> float:
    """Wins ÷ games ended."""
    return t.wins / t.games_ended if t.games_ended else 0


def value_of(t: Tally, metric: Metric) -> float:
    """A tally's value on a metric."""
    values: dict[Metric, float] = {
        Metric.AVG_PER_ROUND: avg_of(t),
        Metric.POINTS: t.points,
        Metric.WINS: t.wins,
        Metric.WIN_RATE: win_rate_of(t),
        Metric.ACCURACY: accuracy_of(t),
        Metric.BEST_ROUND: t.best_round if t.rounds else 0,
        Metric.LONGEST_HOT: t.longest_hot,
        Metric.GAMES: t.games,
        Metric.ROUNDS: t.rounds,
    }
    return values[metric]


def ranked(tallies: Iterable[Tally], metric: Metric, min_rounds: int) -> list[BoardRow]:
    """Qualified first; by value, points, rounds, id; equal values share a rank (1, 1, 3)."""
    rate = metric in RATE_METRICS
    rows = [
        BoardRow(
            rank=None,
            qualified=not rate or t.rounds > min_rounds,
            player_id=t.player_id,
            value=value_of(t, metric),
            points=t.points,
            games=t.games,
            rounds=t.rounds,
            wins=t.wins,
            accuracy=accuracy_of(t),
            longest_hot=t.longest_hot,
        )
        for t in tallies
        # A rate needs something to be a rate of; nobody is listed without a round or an ended game.
        if t.rounds > 0 or t.games_ended > 0
    ]
    rows.sort(key=lambda r: (not r.qualified, -r.value, -r.points, -r.rounds, r.player_id))
    out: list[BoardRow] = []
    rank = 0
    for i, row in enumerate(rows):
        if row.qualified:
            prev = out[i - 1] if i else None
            same = prev is not None and prev.qualified and prev.value == row.value
            rank = rank if same else i + 1
            row = replace(row, rank=rank)
        out.append(row)
    return out


def _delta(was: int | None, now: int | None) -> int | None:
    return None if was is None or now is None else was - now


def leaderboard(
    current: Iterable[Tally],
    previous: Iterable[Tally] | None,
    metric: Metric,
    min_rounds: int,
) -> Board:
    """Ranked rows with rank movement against the previous window (None: no previous window)."""
    rows = ranked(current, metric, min_rounds)
    before = {r.player_id: r.rank for r in ranked(previous or [], metric, min_rounds)}
    with_delta = [replace(r, rank_delta=_delta(before.get(r.player_id), r.rank)) for r in rows]
    return Board(rows=with_delta, min_rounds=min_rounds)


def tallies_from_facts(
    rounds: Iterable[RoundFact], players: Iterable[GameFact], from_ms: int, to_ms: int
) -> dict[str, Tally]:
    """Each player's tally over rounds done in [from, to) and games ended in it (in memory)."""
    out: dict[str, Tally] = {}
    seen: set[tuple[str, str]] = set()
    for r in rounds:
        if not from_ms <= r.done_at < to_ms:
            continue
        first = (r.game_id, r.player_id) not in seen
        seen.add((r.game_id, r.player_id))
        part = Tally(
            player_id=r.player_id,
            points=r.points,
            rounds=1,
            made=int(r.outcome == "made"),
            games=int(first),
            best_round=r.points,
            worst_round=r.points,
            longest_hot=r.run_hot,
            longest_cold=r.run_cold,
            hook_hits=r.hook_hits,
        )
        out[r.player_id] = merge_tallies(out.get(r.player_id, Tally(r.player_id)), part)
    for p in players:
        if p.ended_at is None or p.place is None or not from_ms <= p.ended_at < to_ms:
            continue
        part = Tally(player_id=p.player_id, games_ended=1, wins=int(p.won))
        out[p.player_id] = merge_tallies(out.get(p.player_id, Tally(p.player_id)), part)
    return out
