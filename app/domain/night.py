"""Baithak Wrapped for one night (rewards plan §7.2): games played with gaps of three hours or less.

Mirrors the app's `src/games/kachuful/domain/night.ts` (`nightOf`);
parity is the `night-cases` golden fixture. `link_nights` groups every kind's games across devices.
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.derive import Facts, GameFact, GameInfo, RoundFact

NIGHT_GAP_MS = 3 * 3600 * 1000
"""Games further apart than this are different nights; a night across midnight stays one."""
NIGHT_FRESH_MS = 12 * 3600 * 1000
"""A night is "tonight" until its last game is this old."""
BRAVE_MIN_TABLE = 3


@dataclass(frozen=True)
class NightSeat:
    """One seat of one finished game, any kind; `points` only for Kachuful (the edge)."""

    player_id: str
    won: bool
    points: int | None = None


@dataclass(frozen=True)
class NightGame:
    game_id: str
    kind: str
    created_at: int
    ended_at: int
    seats: tuple[NightSeat, ...]


@dataclass(frozen=True)
class NightPlayer:
    """`edge`: each Kachuful game's points minus that table's average, summed."""

    player_id: str
    wins: int
    games: int
    edge: float


@dataclass(frozen=True)
class NightSwing:
    player_id: str
    points: int


@dataclass(frozen=True)
class NightBid:
    player_id: str
    bid: int
    cards: int


@dataclass(frozen=True)
class Night:
    from_: int
    to: int
    game_ids: tuple[str, ...]
    players: tuple[NightPlayer, ...]
    winner: str | None
    swing: NightSwing | None
    best_bid: NightBid | None
    brave_call: NightBid | None
    game_winners: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class LinkGame:
    game_id: str
    created_at: int
    ended_at: int
    player_ids: frozenset[str]


@dataclass
class _Tally:
    wins: int = 0
    games: int = 0
    edge: float = 0.0


def _tally_game(tally: dict[str, _Tally], game: NightGame) -> None:
    scored = [s.points for s in game.seats if s.points is not None]
    avg = sum(scored) / max(1, len(scored))
    for seat in game.seats:
        x = tally.setdefault(seat.player_id, _Tally())
        x.games += 1
        x.wins += 1 if seat.won else 0
        if seat.points is not None:
            x.edge += seat.points - avg


def _ranked(games: Sequence[NightGame]) -> tuple[NightPlayer, ...]:
    tally: dict[str, _Tally] = {}
    for game in games:
        _tally_game(tally, game)
    players = [NightPlayer(pid, t.wins, t.games, t.edge) for pid, t in tally.items()]
    return tuple(sorted(players, key=lambda p: (-p.wins, -p.edge, p.player_id)))


def _swing(rounds: Sequence[RoundFact]) -> NightSwing | None:
    best: NightSwing | None = None
    for r in rounds:
        if best is None or abs(r.points) > abs(best.points):
            best = NightSwing(r.player_id, r.points)
    return best


def _top_bid(rounds: Sequence[RoundFact], made: bool) -> NightBid | None:
    best: NightBid | None = None
    for r in rounds:
        if (r.outcome == "made") != made or (not made and r.table_size < BRAVE_MIN_TABLE):
            continue
        if best is None or r.bid > best.bid:
            best = NightBid(r.player_id, r.bid, r.cards)
    return best


def night_story(games: Sequence[NightGame], rounds: Sequence[RoundFact]) -> Night:
    """The story of a night's games, given in night order (oldest end first).

    `rounds` are the Kachuful round facts of those games in fact order.
    """
    ranked = _ranked(games)
    return Night(
        from_=games[0].created_at,
        to=games[-1].ended_at,
        game_ids=tuple(g.game_id for g in games),
        players=ranked,
        winner=ranked[0].player_id if ranked and ranked[0].wins else None,
        swing=_swing(rounds),
        best_bid=_top_bid(rounds, made=True),
        brave_call=_top_bid(rounds, made=False),
        game_winners=tuple(tuple(s.player_id for s in g.seats if s.won) for g in games),
    )


def _night_infos(facts: Facts, now: int) -> list[GameInfo]:
    # Facts are oldest-created first; a stable sort keeps that order between equal ends.
    ended = sorted(
        (g for g in facts.games if g.ended_at is not None),
        key=lambda g: -(g.ended_at or 0),
    )
    if not ended or now - (ended[0].ended_at or 0) > NIGHT_FRESH_MS:
        return []
    night = [ended[0]]
    for g in ended[1:]:
        if night[-1].created_at - (g.ended_at or 0) > NIGHT_GAP_MS:
            break
        night.append(g)
    return night[::-1]


def _night_game(info: GameInfo, seats: Sequence[GameFact]) -> NightGame:
    return NightGame(
        game_id=info.game_id,
        kind="kachuful",
        created_at=info.created_at,
        ended_at=info.ended_at or 0,
        seats=tuple(
            NightSeat(p.player_id, p.won, p.points) for p in seats if p.game_id == info.game_id
        ),
    )


def night_of(facts: Facts, now: int) -> Night | None:
    """The app's nightOf: the night of the latest ended game, if it is still tonight."""
    infos = _night_infos(facts, now)
    if not infos:
        return None
    ids = {g.game_id for g in infos}
    seats = [p for p in facts.players if p.game_id in ids]
    rounds = [r for r in facts.rounds if r.game_id in ids]
    games = [_night_game(info, seats) for info in infos]
    return night_story(games, rounds)


def _bid_dict(bid: NightBid | None) -> dict[str, Any] | None:
    if bid is None:
        return None
    return {"playerId": bid.player_id, "bid": bid.bid, "cards": bid.cards}


def night_dict(night: Night) -> dict[str, Any]:
    """The app's Night JSON (camelCase)."""
    swing = night.swing
    return {
        "from": night.from_,
        "to": night.to,
        "gameIds": list(night.game_ids),
        "players": [
            {"playerId": p.player_id, "wins": p.wins, "games": p.games, "edge": p.edge}
            for p in night.players
        ],
        "winner": night.winner,
        "swing": None if swing is None else {"playerId": swing.player_id, "points": swing.points},
        "bestBid": _bid_dict(night.best_bid),
        "braveCall": _bid_dict(night.brave_call),
        "gameWinners": [list(w) for w in night.game_winners],
    }


def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[max(ra, rb)] = min(ra, rb)


def _link_player(parent: list[int], order: list[int], games: Sequence[LinkGame], gap: int) -> None:
    # `order` is one player's games by end: a game links to every earlier end within the gap.
    ends = [games[i].ended_at for i in order]
    for pos, i in enumerate(order):
        for j in order[bisect_left(ends, games[i].created_at - gap, 0, pos) : pos]:
            _union(parent, i, j)


def _end_key(g: LinkGame) -> tuple[int, str]:
    return g.ended_at, g.game_id


def link_nights(games: Sequence[LinkGame], gap_ms: int = NIGHT_GAP_MS) -> list[list[LinkGame]]:
    """Group finished games into nights: connected by a shared player and a gap of at most `gap_ms`.

    Two games link when the later-ended one starts at most `gap_ms` after the earlier one ends
    (overlapping games link too). Groups are sorted by (ended_at, game_id), and by their first game.
    """
    ordered = sorted(games, key=_end_key)
    parent = list(range(len(ordered)))
    by_player: dict[str, list[int]] = {}
    for i, g in enumerate(ordered):
        for pid in g.player_ids:
            by_player.setdefault(pid, []).append(i)
    for order in by_player.values():
        _link_player(parent, order, ordered, gap_ms)
    groups: dict[int, list[LinkGame]] = {}
    for i, g in enumerate(ordered):
        groups.setdefault(_find(parent, i), []).append(g)
    return sorted(groups.values(), key=lambda grp: _end_key(grp[0]))


def night_id_seed(group: Sequence[LinkGame]) -> str:
    """The earliest game's id (by created_at, then id): the night's id is uuid5 of it."""
    return min(group, key=lambda g: (g.created_at, g.game_id)).game_id
