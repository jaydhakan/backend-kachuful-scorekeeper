"""What every game's achievements and awards share (app: src/progression). Pure.

Kachuful's rules (`domain.achievements`, `domain.awards`) predate this module and keep their own
career code; the other games build on the helpers here. Ties share an award; nothing is awarded
when nobody passes the minimum.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.time import hour_of

# Win 1, 2, 3, 5 games in a row (the `wins` ladder).
WIN_RUNS = ((1, "first_win"), (2, "back_to_back"), (3, "hat_trick"), (5, "dynasty"))
CREW_STEPS = (10, 25)

Key = tuple[str, str, str | None, int | None, str | None]


@dataclass(frozen=True)
class Earned:
    """One earned achievement (the contract's Achievement without the row id and game)."""

    code: str
    player_id: str
    game_id: str | None
    round_index: int | None
    period: str | None
    earned_at: int
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Key:
        """Earned at most once per player, code, game, round and period (within one game)."""
        return (self.player_id, self.code, self.game_id, self.round_index, self.period)


@dataclass(frozen=True)
class Award:
    """One daily or season award."""

    code: str
    player_id: str
    value: float
    game_id: str | None = None
    round_index: int | None = None


def best_of(
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


def is_night(ms: int, tz: ZoneInfo) -> bool:
    """Between 1 and 5 am in `tz` (night_owl)."""
    return 1 <= hour_of(ms, tz) < 5


@dataclass(frozen=True)
class PlayedGame:
    """One of a player's finished games, as the shared career rules read it."""

    player_id: str
    game_id: str
    ended_at: int
    won: bool
    others: tuple[str, ...] = field(default=())


def _at(g: PlayedGame, code: str, data: dict[str, Any] | None = None) -> Earned:
    return Earned(code, g.player_id, g.game_id, None, None, g.ended_at, data or {})


def shared_career(
    player_id: str, games: Sequence[PlayedGame], *, games_steps: Sequence[int]
) -> list[Earned]:
    """first_win … dynasty, games_<n> and crew_<n>; `games` in (ended_at, game_id) order.

    The same rules as Kachuful's career: a run code at the game that reaches it, `games_<n>` at the
    n-th game, `crew_<n>` at the game whose table takes the count of other people past n.
    """
    out: list[Earned] = []
    wins = run = 0
    crew: set[str] = set()
    for k, g in enumerate(games):
        if g.won:
            wins += 1
            run += 1
            out += [_at(g, code) for n, code in WIN_RUNS if (wins if n == 1 else run) == n]
        else:
            run = 0
        if k + 1 in games_steps:
            out.append(_at(g, f"games_{k + 1}", {"n": k + 1}))
        before = len(crew)
        crew |= {o for o in g.others if o != player_id}
        out += [_at(g, f"crew_{n}", {"n": n}) for n in CREW_STEPS if before < n <= len(crew)]
    return out


def shared_ladders(player_id: str, games: Sequence[PlayedGame]) -> dict[str, int]:
    """The `wins` (longest winning run), `games` and `crew` ladder counts."""
    longest = run = 0
    crew: set[str] = set()
    for g in games:
        run = run + 1 if g.won else 0
        longest = max(longest, run)
        crew |= {o for o in g.others if o != player_id}
    return {"wins": longest, "games": len(games), "crew": len(crew)}


@dataclass(frozen=True)
class WinTally:
    """One player's finished games and wins over a month."""

    player_id: str
    games: int
    wins: int


@dataclass(frozen=True)
class StandingRow:
    """One player in a month's win-rate standings; `rank` only once qualified."""

    rank: int | None
    qualified: bool
    player_id: str
    value: float
    games: int
    wins: int


def win_rate_standings(tallies: Iterable[WinTally], min_games: int) -> list[StandingRow]:
    """Qualified (at least `min_games`) first, by win rate, then more games; ties share (1, 1, 3)."""
    rows = [
        StandingRow(
            rank=None,
            qualified=t.games >= min_games,
            player_id=t.player_id,
            value=t.wins / t.games,
            games=t.games,
            wins=t.wins,
        )
        for t in tallies
        if t.games > 0
    ]
    rows.sort(key=lambda r: (not r.qualified, -r.value, -r.games, r.player_id))
    out: list[StandingRow] = []
    rank = 0
    for i, row in enumerate(rows):
        if row.qualified:
            prev = out[i - 1] if i else None
            same = (
                prev is not None
                and prev.qualified
                and (prev.value, prev.games) == (row.value, row.games)
            )
            rank = rank if same else i + 1
            row = replace(row, rank=rank)
        out.append(row)
    return out


def podium_awards(standings: Sequence[StandingRow]) -> list[Award]:
    """season_champion, season_runner_up and season_third: ranks 1-3 (ties share)."""
    places = ((1, "season_champion"), (2, "season_runner_up"), (3, "season_third"))
    return [
        Award(code, r.player_id, r.value)
        for rank, code in places
        for r in standings
        if r.qualified and r.rank == rank
    ]


def iron_awards(standings: Sequence[StandingRow]) -> list[Award]:
    """season_iron: the most games among the qualified."""
    return best_of(((r.player_id, r.games) for r in standings if r.qualified), "season_iron")


def climber_awards(standings: Sequence[StandingRow], last: Sequence[StandingRow]) -> list[Award]:
    """season_climber: the biggest rise in rank since last month (ranked both months)."""
    before = {r.player_id: r.rank for r in last if r.rank is not None}
    return best_of(
        (
            (r.player_id, before[r.player_id] - r.rank)
            for r in standings
            if r.rank is not None and r.player_id in before
        ),
        "season_climber",
        ok=lambda v: v > 0,
    )
