"""The trick on the table (app: trick.ts)."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.cards import rank_value
from app.domain.spades.types import Seat


def led_suit(trick: Sequence[dict[str, Any]]) -> str | None:
    """The suit of the trick's first card, or None for an empty trick."""
    if not trick:
        return None
    suit: str = trick[0]["card"]["suit"]
    return suit


def trick_winner(plays: Sequence[dict[str, Any]]) -> Seat:
    """The highest spade, or with none the highest card of the suit led."""
    best = plays[0]
    for p in plays[1:]:
        a, b = p["card"], best["card"]
        if (rank_value(a) > rank_value(b)) if a["suit"] == b["suit"] else a["suit"] == "spades":
            best = p
    seat: Seat = best["seat"]
    return seat
