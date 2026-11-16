"""The turn timer (app: timer.ts): deadlines, the away timer, and what a timeout bids or plays."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.bidding import min_bid_for
from app.domain.spades.cards import lowest_card
from app.domain.spades.legal import legal_plays
from app.domain.spades.types import Card, Seat, State

# The timer an away seat plays on.
AWAY_TIMER_MS = 5000
# Timeouts in a row that mark a seat away.
AWAY_AFTER = 2
# With the timer off, a seat unseen this long plays as a timeout would.
ABSENT_MS = 60_000
# After this long in 'deal' or 'handEnd', anyone seated may deal.
DEAL_ANYONE_MS = 15_000


def timed_turn(s: State) -> bool:
    """Whether there is a bid or play that a timeout could make for the seat to act."""
    return (
        s["status"] == "playing"
        and s["paused"] is None
        and s["phase"] in ("bidding", "playing")
        and s["turn"] is not None
        and s["turnStartedAt"] is not None
    )


def deadline_for(s: State, last_seen_at: int | None = None) -> int | None:
    """When the turn may be timed out: the away timer, the rules' timer, else 60 s after last seen."""
    if not timed_turn(s):
        return None
    start: int = s["turnStartedAt"]
    if s["seats"][s["turn"]]["away"]:
        return start + AWAY_TIMER_MS
    timer = s["rules"]["timerSec"]
    if timer is not None:
        out: int = start + timer * 1000
        return out
    if last_seen_at is None:
        return None
    return max(start, last_seen_at) + ABSENT_MS


def timeout_bid(seat: Seat, bids: Sequence[Any], r: dict[str, Any]) -> int:
    """The lowest bid the rules allow that isn't Nil."""
    return min_bid_for(seat, bids, r)


def timeout_play(hand: Sequence[Card], trick: Sequence[dict[str, Any]], broken: bool) -> Card:
    """The lowest legal card."""
    return lowest_card(legal_plays(hand, trick, broken))
