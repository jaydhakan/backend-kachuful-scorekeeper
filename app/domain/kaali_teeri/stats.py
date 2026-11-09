"""Derived rows, summary and player stats (app: stats.ts). Stats read the stored rows, never payloads."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.kaali_teeri.game import KtGame, KtHand, KtSeat, Sweep
from app.domain.kaali_teeri.scoring import hand_score, result, slots, standings, team, totals

_SPACES = re.compile(r"\s+")


def player_key(seat: KtSeat) -> str:
    """A player across games: their registry id, else their name (as Kachuful keys them)."""
    if seat.player_id is not None:
        return seat.player_id
    return "name:" + _SPACES.sub(" ", seat.name.strip()).lower()


@dataclass(frozen=True)
class KtHandRow:
    """A `kt_hands` row: one done hand, players by key, plus every player's score."""

    hand_index: int
    dealer_id: str
    bidder_id: str
    bid: int
    forced: bool
    trump: str
    partner_ids: tuple[str, ...]
    slots: dict[str, int]
    team_points: int
    made: bool
    sweep: Sweep
    done_at: int | None
    scores: dict[str, int]

    @property
    def team(self) -> tuple[str, ...]:
        """The bidder, then each partner."""
        return (self.bidder_id, *self.partner_ids)


@dataclass(frozen=True)
class KtGamePlayerRow:
    """A `kt_game_players` row; `place` only once the game has ended."""

    player_id: str
    points: int
    place: int | None
    won: bool
    hands_bid: int
    bids_made: int
    times_partner: int
    partner_made: int
    double_slots: int
    sweeps: int


def _keys(game: KtGame) -> dict[str, str]:
    return {p.id: player_key(p) for p in game.players}


def _hand_row(game: KtGame, hand: KtHand, key: dict[str, str]) -> KtHandRow:
    res = result(hand, game.rules)
    assert res is not None, "done hands have points"
    scores = hand_score(hand, game.rules, list(key))
    return KtHandRow(
        hand_index=int(hand.index),
        dealer_id=player_key(game.players[int(hand.dealer_index)]),
        bidder_id=key[hand.bidder],
        bid=int(hand.bid),
        forced=any("forced" in c.keys for c in hand.calls),
        trump=str(hand.trump),
        partner_ids=tuple(key[p] for p in team(hand)[1:]),
        slots={key[p]: v for p, v in slots(hand).items()},
        team_points=int(hand.team_points),
        made=res.made,
        sweep=res.sweep,
        done_at=None if not isinstance(hand.done_at, int) else hand.done_at,
        scores={key[seat]: v for seat, v in scores.items()},
    )


def hand_rows(game: KtGame) -> list[KtHandRow]:
    """The kt_hands rows of a validated game's done hands."""
    key = _keys(game)
    return [_hand_row(game, h, key) for h in game.done_hands]


@dataclass(frozen=True)
class _Part:
    """What one player did in one hand, the unit every count is built from."""

    bidder: bool
    partner: bool
    made: bool
    bid: int
    slots: int
    swept: bool
    mates: tuple[str, ...]


def _part(row: KtHandRow, key: str) -> _Part:
    on_team = key in row.team
    return _Part(
        bidder=row.bidder_id == key,
        partner=on_team and row.bidder_id != key,
        made=row.made,
        bid=row.bid,
        slots=row.slots.get(key, 0),
        swept=row.sweep == (Sweep.BIDDERS if on_team else Sweep.DEFENDERS),
        mates=tuple(k for k in row.team if k != key) if on_team else (),
    )


def _counts(parts: Sequence[_Part]) -> dict[str, int]:
    return {
        "hands_bid": sum(1 for x in parts if x.bidder),
        "bids_made": sum(1 for x in parts if x.bidder and x.made),
        "times_partner": sum(1 for x in parts if x.partner),
        "partner_made": sum(1 for x in parts if x.partner and x.made),
        "double_slots": sum(1 for x in parts if x.slots >= 2),
        "sweeps": sum(1 for x in parts if x.swept),
    }


def game_player_rows(game: KtGame) -> list[KtGamePlayerRow]:
    """The kt_game_players rows, in seat order."""
    ended = game.status == "finished"
    rank = {s.seat.id: s.rank for s in standings(game)}
    points = totals(game)
    rows = hand_rows(game)
    out = []
    for seat in game.players:
        key = player_key(seat)
        place = rank[seat.id] if ended else None
        out.append(
            KtGamePlayerRow(
                player_id=key,
                points=points[seat.id],
                place=place,
                won=place == 1,
                **_counts([_part(r, key) for r in rows]),
            )
        )
    return out


def summary(game: KtGame) -> dict[str, Any]:
    """The KtSummary JSON the app reads in lists."""
    ended = game.status == "finished"
    by_seat = {s.seat.id: s for s in standings(game)}
    return {
        "kind": "kaali-teeri",
        "status": game.status,
        "hands": len(game.done_hands),
        "length": game.rules.length,
        "players": [
            {
                "playerId": p.player_id,
                "name": p.name,
                "emoji": p.emoji,
                "color": p.color,
                "points": by_seat[p.id].total,
                "place": by_seat[p.id].rank if ended else None,
            }
            for p in game.players
        ],
    }


@dataclass(frozen=True)
class KtSeatResult:
    """One player's points and place (None until the game ended) in a stored game."""

    points: int
    place: int | None


@dataclass(frozen=True)
class KtStatsGame:
    """A stored game as stats read it: seats by player key, done hands by index."""

    game_id: str
    created_at: int
    ended: bool
    seats: dict[str, KtSeatResult]
    hands: tuple[KtHandRow, ...]


def stats_game(game: KtGame) -> KtStatsGame:
    """A KtStatsGame from a payload (tests; the server builds it from stored rows)."""
    return KtStatsGame(
        game_id=game.id,
        created_at=game.created_at,
        ended=game.status == "finished",
        seats={r.player_id: KtSeatResult(r.points, r.place) for r in game_player_rows(game)},
        hands=tuple(hand_rows(game)),
    )


@dataclass(frozen=True)
class KtBestGame:
    """The highest-scoring ended game (the earliest wins a tie)."""

    game_id: str
    points: int


@dataclass(frozen=True)
class KtMostPartner:
    """The player most often on the same bidding team, and how those hands went."""

    player_id: str
    hands: int
    made: int


@dataclass(frozen=True)
class KtStreak:
    """Made bids in a row: the run now, and the longest."""

    current: int
    best: int


@dataclass(frozen=True)
class KtPlayerStats:
    """One player's plan §6 stats over a set of games."""

    games: int
    wins: int
    win_rate: float | None
    avg_place: float | None
    points: int
    best_game: KtBestGame | None
    hands_bid: int
    bids_made: int
    made_rate: float | None
    avg_bid: float | None
    highest_made: int | None
    hands_lost: int
    sweeps: int
    times_partner: int
    partner_made: int
    partner_win_rate: float | None
    double_slots: int
    most_partner: KtMostPartner | None
    made_streak: KtStreak


def _rate(a: int, b: int) -> float | None:
    return a / b if b else None


def by_play(games: Sequence[KtStatsGame]) -> list[KtStatsGame]:
    """Games by start, then id (TS localeCompare orders lowercase ids and keys like str does)."""
    return sorted(games, key=lambda g: (g.created_at, g.game_id))


def _most_partner(parts: Sequence[_Part]) -> KtMostPartner | None:
    mates: dict[str, tuple[int, int]] = {}
    for x in parts:
        for m in x.mates:
            hands, made = mates.get(m, (0, 0))
            mates[m] = (hands + 1, made + int(x.made))
    if not mates:
        return None
    top = min(mates.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    return KtMostPartner(player_id=top[0], hands=top[1][0], made=top[1][1])


def _streak(bids: Sequence[_Part]) -> KtStreak:
    current = longest = 0
    for x in bids:
        current = current + 1 if x.made else 0
        longest = max(longest, current)
    return KtStreak(current=current, best=longest)


def _best(best: KtBestGame | None, game: KtStatsGame, points: int) -> KtBestGame | None:
    if best is None or points > best.points:
        return KtBestGame(game_id=game.game_id, points=points)
    return best


def player_stats(games: Sequence[KtStatsGame], key: str) -> KtPlayerStats:
    """One player's stats; wins, places and the best game count ended games only."""
    parts: list[_Part] = []
    places: list[int] = []
    count = points = 0
    best: KtBestGame | None = None
    for game in by_play(games):
        me = game.seats.get(key)
        if me is None or not game.hands:
            continue
        count += 1
        points += me.points
        parts += [_part(h, key) for h in game.hands]
        if game.ended and me.place is not None:
            places.append(me.place)
            best = _best(best, game, me.points)
    bids = [x for x in parts if x.bidder]
    made = [x for x in bids if x.made]
    partner = [x for x in parts if x.partner]
    partner_made = sum(1 for x in partner if x.made)
    wins = sum(1 for p in places if p == 1)
    return KtPlayerStats(
        games=count,
        wins=wins,
        win_rate=_rate(wins, len(places)),
        avg_place=_rate(sum(places), len(places)),
        points=points,
        best_game=best,
        hands_bid=len(bids),
        bids_made=len(made),
        made_rate=_rate(len(made), len(bids)),
        avg_bid=_rate(sum(x.bid for x in bids), len(bids)),
        highest_made=max((x.bid for x in made), default=None),
        hands_lost=len(bids) - len(made),
        sweeps=sum(1 for x in parts if x.swept),
        times_partner=len(partner),
        partner_made=partner_made,
        partner_win_rate=_rate(partner_made, len(partner)),
        double_slots=sum(1 for x in parts if x.slots >= 2),
        most_partner=_most_partner(parts),
        made_streak=_streak(bids),
    )


@dataclass
class KtHeadToHead:
    """Two players across the games both sat in with a hand played."""

    games: int = 0
    a_ahead: int = 0
    b_ahead: int = 0
    level: int = 0
    together: int = 0
    together_made: int = 0
    a_bid_against_b: int = 0
    a_made_against_b: int = 0
    b_bid_against_a: int = 0
    b_made_against_a: int = 0


def _count_places(out: KtHeadToHead, a: KtSeatResult, b: KtSeatResult) -> None:
    if a.place is None or b.place is None:
        return
    if a.place < b.place:
        out.a_ahead += 1
    elif b.place < a.place:
        out.b_ahead += 1
    else:
        out.level += 1


def _count_hand(out: KtHeadToHead, row: KtHandRow, a: str, b: str) -> None:
    # Together is checked first, so a bidder's opponent here is always defending.
    made = int(row.made)
    if a in row.team and b in row.team:
        out.together += 1
        out.together_made += made
    elif row.bidder_id == a:
        out.a_bid_against_b += 1
        out.a_made_against_b += made
    elif row.bidder_id == b:
        out.b_bid_against_a += 1
        out.b_made_against_a += made


def head_to_head(games: Sequence[KtStatsGame], a: str, b: str) -> KtHeadToHead:
    """Places in ended games, and hands together or against each other."""
    out = KtHeadToHead()
    for game in games:
        seat_a, seat_b = game.seats.get(a), game.seats.get(b)
        if seat_a is None or seat_b is None or not game.hands:
            continue
        out.games += 1
        if game.ended:
            _count_places(out, seat_a, seat_b)
        for row in game.hands:
            _count_hand(out, row, a, b)
    return out
