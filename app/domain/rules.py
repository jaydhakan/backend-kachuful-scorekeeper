"""Card counts, scoring, standings and the results check (app: cards, rounds, scoring, tricks, standings)."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.game import Game, Round, Seat

TRUMPS = ("spades", "diamonds", "clubs", "hearts")
PEAK = 10
DECK_SIZE = 52
MIN_PLAYERS = 3
MAX_PLAYERS = {1: 7, 2: 10}


def is_int(value: Any) -> bool:
    """JavaScript's Number.isInteger: integral numbers (2.0 included), never booleans."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and float(value).is_integer()


def player_counts(decks: Any) -> range:
    """Table sizes a number of decks allows (none for an unknown deck count)."""
    if decks not in MAX_PLAYERS or isinstance(decks, bool):
        return range(0)
    return range(MIN_PLAYERS, MAX_PLAYERS[decks] + 1)


def max_for(players: int, decks: int) -> int:
    """Most cards a player holds: what the decks allow, never more than 10."""
    return min(PEAK, (DECK_SIZE * decks) // players)


def cycle_len(max_cards: int) -> int:
    """Rounds in one 3 → max → 3 cycle."""
    return 2 * (max_cards - 2)


def cards_for(index: int, max_cards: int) -> int:
    """Cards dealt in round `index`: 3 → max → 3, with max and 3 each repeated once."""
    p = index % cycle_len(max_cards)
    return 3 + p if p < max_cards - 2 else max_cards - (p - (max_cards - 2))


def total_rounds(game: Game) -> float:
    """How many rounds the length mode allows (endless: infinite)."""
    if game.length_mode == "endless":
        return math.inf
    return cycle_len(game.max_cards) * (1 if game.length_mode == "1cycle" else 2)


def points(round_: Round, seat_id: str) -> int | None:
    """Made: +10 * max(bid, 1); missed: the negative. None until bid and result exist."""
    bid = round_.bids.get(seat_id)
    result = round_.results.get(seat_id)
    if bid is None or not result:
        return None
    value = max(int(bid), 1) * 10
    return value if result == "made" else -value


def totals(game: Game) -> dict[str, int]:
    """Each seat's total over the done rounds, by seat id."""
    out = {seat.id: 0 for seat in game.players}
    for round_ in game.done_rounds:
        for seat in game.players:
            value = points(round_, seat.id)
            if value is not None:
                out[seat.id] += value
    return out


@dataclass(frozen=True)
class Standing:
    """A seat's total and competition rank (1, 1, 3)."""

    seat: Seat
    seat_index: int
    total: int
    rank: int


def standings(game: Game) -> list[Standing]:
    """Seats by total, ties in seat order, with competition ranks."""
    by_seat = totals(game)
    order = sorted(
        ((seat, i, by_seat[seat.id]) for i, seat in enumerate(game.players)),
        key=lambda x: (-x[2], x[1]),
    )
    out: list[Standing] = []
    for k, (seat, i, total) in enumerate(order):
        rank = k + 1 if k == 0 or total != out[k - 1].total else out[k - 1].rank
        out.append(Standing(seat=seat, seat_index=i, total=total, rank=rank))
    return out


def _share_out(bids_of_missed: Iterable[int], target: int) -> bool:
    """Whether `target` tricks can go to these players with none winning exactly their bid."""
    sums = {0}
    for bid in bids_of_missed:
        sums = {s + t for s in sums for t in range(target - s + 1) if t != bid}
    return target in sums


def fits(cards: int, ids: list[str], bids: Mapping[str, Any], marks: Mapping[str, Any]) -> bool:
    """Whether made/missed marks can add up to the tricks played."""
    left = cards - sum(int(bids.get(pid) or 0) for pid, m in marks.items() if m == "made")
    if left < 0:
        return False
    missed = [int(bids.get(pid) or 0) for pid in ids if marks.get(pid) == "missed"]
    return _share_out(missed, left)
