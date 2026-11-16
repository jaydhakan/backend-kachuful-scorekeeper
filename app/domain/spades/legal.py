"""Which cards may be played (app: legal.ts)."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.cards import same_card
from app.domain.spades.trick import led_suit
from app.domain.spades.types import Card


def can_lead(card: Card, hand: Sequence[Card], broken: bool) -> bool:
    """Spades lead only once broken, or from a hand of nothing but spades."""
    return broken or card["suit"] != "spades" or all(c["suit"] == "spades" for c in hand)


def legal_plays(hand: Sequence[Card], trick: Sequence[dict[str, Any]], broken: bool) -> list[Card]:
    """Every card the hand may play now."""
    led = led_suit(trick)
    if led is None:
        return [c for c in hand if can_lead(c, hand, broken)]
    follow = [c for c in hand if c["suit"] == led]
    return follow if follow else list(hand)


def play_problem(
    card: Card, hand: Sequence[Card], trick: Sequence[dict[str, Any]], broken: bool
) -> str | None:
    """Why a card can't be played now (`not-in-hand`, `must-follow`, `spades-locked`), or None."""
    if not any(same_card(c, card) for c in hand):
        return "not-in-hand"
    led = led_suit(trick)
    if led is None:
        return None if can_lead(card, hand, broken) else "spades-locked"
    if card["suit"] != led and any(c["suit"] == led for c in hand):
        return "must-follow"
    return None
