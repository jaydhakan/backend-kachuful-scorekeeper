"""Spades' derived rows and board tallies, for both kinds (table mode and online).

A finished game derives one row per player: the game's result for whoever sat at the end, and the
hands each person played (a Replace hands a seat over mid-game: each hand counts for who played
it). A cancelled game derives nothing; a draw is played, not won. Stats read the rows only.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.spades.seats import partner_of, team_of
from app.domain.spades.standings import hand_lines, scores_of, totals

# A win rate ranks only from this many games.
MIN_RATED_GAMES = 3


@dataclass
class SpTally:
    """Hand counts for one player: bids are non-Nil; made and set are the team's contract."""

    hands: int = 0
    bids: int = 0
    bids_made: int = 0
    nils: int = 0
    nils_made: int = 0
    blind_nils: int = 0
    blind_nils_made: int = 0
    bags: int = 0
    sets: int = 0

    def add(self, other: "SpTally") -> None:
        """Add another tally into this one."""
        for name in _COUNT_NAMES:
            setattr(self, name, getattr(self, name) + getattr(other, name))


_COUNT_NAMES = tuple(SpTally.__dataclass_fields__)


@dataclass(frozen=True)
class SpPlayerRow:
    """A `spades_game_players` row; `result` is None for someone who left before the end."""

    player_id: str
    seat: int
    team: str
    partner_id: str | None
    result: str | None
    score: int
    opp_score: int
    tally: SpTally


def _result_for(team: str, winner: str) -> str:
    if winner == "draw":
        return "draw"
    return "win" if winner == team else "loss"


def _count_hand(tally: SpTally, hand: dict[str, Any], seat: int, line: dict[str, Any]) -> None:
    team = line[team_of(seat)]
    tally.hands += 1
    tally.bags += int(team["bags"])
    if hand["bids"][seat] == 0:
        made = any(n["seat"] == seat and n["made"] for n in team["nils"])
        if hand["blind"][seat]:
            tally.blind_nils += 1
            tally.blind_nils_made += int(made)
        else:
            tally.nils += 1
            tally.nils_made += int(made)
        return
    tally.bids += 1
    tally.bids_made += int(team["made"] is True)
    tally.sets += int(team["made"] is False)


def _hand_players(hand: dict[str, Any], seats: Sequence[str | None]) -> Sequence[str | None]:
    """Who played each seat: the hand's own record online, else the game's seats."""
    played = hand.get("players")
    return played if isinstance(played, list) and len(played) == 4 else seats


@dataclass
class _Seen:
    tally: SpTally = field(default_factory=SpTally)
    seat: int = 0
    partner: str | None = None


def _tallies(hands: Sequence[dict[str, Any]], seats: Sequence[str | None]) -> dict[str, _Seen]:
    lines = {line["index"]: line for line in hand_lines(hands)}
    seen: dict[str, _Seen] = {}
    for hand in hands:
        line = lines.get(hand.get("index"))
        if line is None:
            continue
        players = _hand_players(hand, seats)
        for seat, pid in enumerate(players):
            if pid is None:
                continue
            entry = seen.setdefault(pid, _Seen())
            entry.seat, entry.partner = seat, players[partner_of(seat)]
            _count_hand(entry.tally, hand, seat, line)
    return seen


def player_rows(
    hands: Sequence[dict[str, Any]], seats: Sequence[str | None], result: dict[str, Any]
) -> list[SpPlayerRow]:
    """Rows of a finished game: `seats` are the registry players seated at the end (by seat)."""
    scores = scores_of(totals(hands))
    seen = _tallies(hands, seats)
    rows: dict[str, SpPlayerRow] = {}
    for pid, entry in seen.items():
        team = team_of(entry.seat)
        rows[pid] = _row(pid, entry.seat, entry.partner, None, scores, entry.tally, team)
    for seat, final in enumerate(seats):
        if final is None:
            continue
        team = team_of(seat)
        tally = seen[final].tally if final in seen else SpTally()
        outcome = _result_for(team, str(result["winner"]))
        rows[final] = _row(final, seat, seats[partner_of(seat)], outcome, scores, tally, team)
    return list(rows.values())


def _row(
    pid: str,
    seat: int,
    partner: str | None,
    result: str | None,
    scores: dict[str, Any],
    tally: SpTally,
    team: str,
) -> SpPlayerRow:
    other = "b" if team == "a" else "a"
    return SpPlayerRow(
        player_id=pid,
        seat=seat,
        team=team,
        partner_id=partner,
        result=result,
        score=int(scores[team]),
        opp_score=int(scores[other]),
        tally=tally,
    )


@dataclass(frozen=True)
class SpGameFact:
    """One stored row as the boards read it."""

    game_id: str
    player_id: str
    team: str
    partner_id: str | None
    result: str | None
    score: int
    tally: SpTally


@dataclass
class SpTotals:
    """A player's totals over some games."""

    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    best_score: int | None = None
    tally: SpTally = field(default_factory=SpTally)


def _rate(a: int, b: int) -> float | None:
    return a / b if b else None


def total_facts(facts: Iterable[SpGameFact]) -> SpTotals:
    """Games, results and hand counts; only rows with a result count as games."""
    out = SpTotals()
    for f in facts:
        out.tally.add(f.tally)
        if f.result is None:
            continue
        out.games += 1
        out.wins += f.result == "win"
        out.draws += f.result == "draw"
        out.losses += f.result == "loss"
        out.best_score = f.score if out.best_score is None else max(out.best_score, f.score)
    return out


def rates(t: SpTotals) -> dict[str, float | None]:
    """The derived rates of a player's totals."""
    s = t.tally
    return {
        "win_rate": _rate(t.wins, t.games),
        "made_rate": _rate(s.bids_made, s.bids),
        "nil_rate": _rate(s.nils_made, s.nils),
        "blind_nil_rate": _rate(s.blind_nils_made, s.blind_nils),
        "bags_per_hand": _rate(s.bags, s.hands),
    }


@dataclass(frozen=True)
class SpBoardRow:
    """One ranked player: rank None when unranked for the sort (too few games or bids)."""

    rank: int | None
    player_id: str
    value: float
    totals: SpTotals


def _sort_value(t: SpTotals, sort: str) -> float | None:
    if sort == "wins":
        return float(t.wins)
    if sort == "rate":
        return t.wins / t.games if t.games >= MIN_RATED_GAMES else None
    return _rate(t.tally.bids_made, t.tally.bids)


def rank_board(by_player: dict[str, SpTotals], sort: str) -> list[SpBoardRow]:
    """Players with a game or a hand, best first; ties share a rank, then games, then id."""
    scored = [(pid, t, _sort_value(t, sort)) for pid, t in by_player.items()]
    ranked = sorted(
        (x for x in scored if x[2] is not None), key=lambda x: (-(x[2] or 0.0), -x[1].games, x[0])
    )
    rest = sorted((x for x in scored if x[2] is None), key=lambda x: (-x[1].games, x[0]))
    out: list[SpBoardRow] = []
    prev: float | None = None
    rank = 0
    for i, (pid, t, value) in enumerate(ranked):
        assert value is not None
        if value != prev:
            rank, prev = i + 1, value
        out.append(SpBoardRow(rank=rank, player_id=pid, value=value, totals=t))
    out += [SpBoardRow(rank=None, player_id=pid, value=0.0, totals=t) for pid, t, _ in rest]
    return out


@dataclass
class SpPair:
    """Two partners' record together (games with a result)."""

    a: str
    b: str
    games: int = 0
    wins: int = 0
    draws: int = 0


def pairs(facts: Iterable[SpGameFact]) -> list[SpPair]:
    """Partner pairs with games together: most wins, then win rate, then games, then ids."""
    found: dict[tuple[str, str], SpPair] = {}
    for f in facts:
        if f.result is None or f.partner_id is None or f.player_id > f.partner_id:
            continue
        pair = found.setdefault((f.player_id, f.partner_id), SpPair(f.player_id, f.partner_id))
        pair.games += 1
        pair.wins += f.result == "win"
        pair.draws += f.result == "draw"
    return sorted(found.values(), key=lambda p: (-p.wins, -p.wins / p.games, -p.games, p.a, p.b))


@dataclass
class SpPartner:
    """One partner of a player and their record together."""

    player_id: str
    games: int = 0
    wins: int = 0


def partners_of(facts: Iterable[SpGameFact], key: str) -> list[SpPartner]:
    """A player's partners (games with a result), most games together first."""
    found: dict[str, SpPartner] = {}
    for f in facts:
        if f.player_id != key or f.result is None or f.partner_id is None:
            continue
        partner = found.setdefault(f.partner_id, SpPartner(f.partner_id))
        partner.games += 1
        partner.wins += f.result == "win"
    return sorted(found.values(), key=lambda p: (-p.games, -p.wins, p.player_id))


@dataclass
class SpHeadToHead:
    """Two players across the finished games both had a result in."""

    games: int = 0
    together: int = 0
    together_wins: int = 0
    against: int = 0
    a_wins: int = 0
    b_wins: int = 0
    draws: int = 0


def head_to_head(facts: Iterable[SpGameFact], a: str, b: str) -> SpHeadToHead:
    """Partners or opponents, game by game."""
    by_game: dict[str, dict[str, SpGameFact]] = {}
    for f in facts:
        if f.result is not None and f.player_id in (a, b):
            by_game.setdefault(f.game_id, {})[f.player_id] = f
    out = SpHeadToHead()
    for seats in by_game.values():
        fa, fb = seats.get(a), seats.get(b)
        if fa is None or fb is None:
            continue
        out.games += 1
        if fa.team == fb.team:
            out.together += 1
            out.together_wins += fa.result == "win"
            continue
        out.against += 1
        out.a_wins += fa.result == "win"
        out.b_wins += fb.result == "win"
        out.draws += fa.result == "draw"
    return out
