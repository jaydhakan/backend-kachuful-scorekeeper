"""Dealer, speaking order and the turn-by-turn auction (app: auction.ts)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.kaali_teeri.cards import deck_total
from app.domain.kaali_teeri.game import KtCall, KtGame, KtRules, js_number, same
from app.domain.rules import is_int


def dealer_for(game: KtGame, index: int) -> int:
    """The dealer's seat for hand `index`; `direction` only lays the seats out, as in Kachuful."""
    return (int(game.first_dealer_index) + index) % len(game.players)


def first_speaker(game: KtGame, index: int) -> int:
    """The seat after the dealer speaks first."""
    return (dealer_for(game, index) + 1) % len(game.players)


def speaking_order(game: KtGame, index: int) -> list[str]:
    """Seat ids in speaking order for hand `index`, starting with the first speaker."""
    n, first = len(game.players), first_speaker(game, index)
    return [game.players[(first + k) % n].id for k in range(n)]


@dataclass(frozen=True)
class AuctionState:
    """Where an auction stands; `invalid_at` is the first illegal call (the state is up to it)."""

    high: Any
    holder: Any
    to_speak: str | None
    done: bool
    forced_to: str | None
    invalid_at: int | None


def valid_bid(bid: Any, r: KtRules) -> bool:
    """A legal winning bid: the minimum up to the whole deck, on the step."""
    return is_int(bid) and r.min_bid <= bid <= deck_total(int(r.decks)) and bid % r.step == 0


def _legal(call: KtCall, expected: str, high: Any, forced_to: str | None, r: KtRules) -> bool:
    if not same(call.player_id, expected):
        return False
    if "forced" in call.keys:
        return forced_to is not None and same(call.forced, r.min_bid)
    if forced_to is not None:
        return False
    if "pass" in call.keys:
        return True
    return valid_bid(call.bid, r) and (high is None or call.bid > js_number(high))


def auction(calls: Sequence[KtCall], order: Sequence[str], r: KtRules) -> AuctionState:
    """Replay the calls; `order[0]` speaks first and is the one forced when nobody bids."""
    n = len(order)
    high: Any = None
    holder: Any = None
    turn = passes = 0
    done = False
    for i, call in enumerate(calls):
        forced_to = order[0] if high is None and passes == n else None
        if done or not _legal(call, order[turn % n], high, forced_to, r):
            to_speak = None if done else order[turn % n]
            return AuctionState(high, holder, to_speak, done, forced_to, i)
        if "pass" in call.keys:
            passes += 1
        else:
            high = call.bid if "bid" in call.keys else call.forced
            holder, passes = call.player_id, 0
        turn += 1
        done = "forced" in call.keys or (high is not None and passes == n - 1)
    forced_to = order[0] if high is None and passes == n else None
    return AuctionState(high, holder, None if done else order[turn % n], done, forced_to, None)


def valid_call(calls: Sequence[KtCall], order: Sequence[str], r: KtRules, call: KtCall) -> bool:
    """Whether `call` may come next."""
    return auction([*calls, call], order, r).invalid_at is None


def next_bid(s: AuctionState, r: KtRules) -> int | None:
    """The smallest bid the next speaker may make; None when only a pass (or the forced bid) is left."""
    if s.done or s.forced_to:
        return None
    bid = int(r.min_bid) if s.high is None else int(s.high) + int(r.step)
    return bid if bid <= deck_total(int(r.decks)) else None
