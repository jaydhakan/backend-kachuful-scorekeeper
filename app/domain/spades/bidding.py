"""Bids (app: bidding.ts): order, Nil, Blind Nil and the minimum team bid."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.seats import SEATS, next_seat, partner_of
from app.domain.spades.types import Seat, at, is_integer, js_num, js_truthy, num_eq

MAX_BID = 13


def bid_order(dealer: Seat) -> list[Seat]:
    """Bidding starts left of the dealer and goes clockwise; the dealer bids last."""
    return [(dealer + 1 + i) % 4 for i in range(len(SEATS))]


def first_to_act(dealer: Seat) -> Seat:
    """The seat left of the dealer."""
    return next_seat(dealer)


def nil_allowed(r: dict[str, Any]) -> bool:
    """Whether Nil may be bid."""
    return js_truthy(r.get("nil"))


def blind_allowed(r: dict[str, Any]) -> bool:
    """Whether Blind Nil may be bid (it needs Nil)."""
    return js_truthy(r.get("nil")) and js_truthy(r.get("blindNil"))


def min_bid_for(seat: Seat, bids: Sequence[Any], r: dict[str, Any]) -> int:
    """The lowest bid other than Nil: the second partner may not take the team below the minimum."""
    p = at(list(bids), partner_of(seat))
    m = r.get("minTeamBid")
    if m is None or p is None or not isinstance(p, int | float) or num_eq(p, 0):
        return 1
    least: int = max(1, js_num(m - p))
    return least


def bid_problem(bid: object, seat: Seat, bids: Sequence[Any], r: dict[str, Any]) -> str | None:
    """Why a bid isn't allowed (`bad-bid`, `nil-off`, `below-min`), or None."""
    if not is_integer(bid) or not isinstance(bid, int | float) or bid < 0 or bid > MAX_BID:
        return "bad-bid"
    if bid == 0:
        return None if js_truthy(r.get("nil")) else "nil-off"
    return "below-min" if bid < min_bid_for(seat, bids, r) else None


def allowed_bids(seat: Seat, bids: Sequence[Any], r: dict[str, Any]) -> list[int]:
    """Every bid the seat may make, Nil (0) first when allowed."""
    out = [0] if js_truthy(r.get("nil")) else []
    return out + list(range(min_bid_for(seat, bids, r), MAX_BID + 1))
