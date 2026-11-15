"""Cards (app: cards.ts and src/lib/cards.ts): ranks, suits, the deck and the orders the game sorts by."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.types import Card, Seat

SUITS: tuple[str, ...] = ("spades", "hearts", "diamonds", "clubs")
RANKS: tuple[str, ...] = ("A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2")

_RANK_VALUE = {r: 14 - i for i, r in enumerate(RANKS)}
# Bridge order, for the draw for deal's ties and the timeout's choice between equal ranks.
_SUIT_RANK = {"clubs": 0, "diamonds": 1, "hearts": 2, "spades": 3}
# Colours alternate with spades on the right.
_DISPLAY = {"diamonds": 0, "clubs": 1, "hearts": 2, "spades": 3}

# Cards in each hand, and tricks in each hand.
TRICKS = 13


def rank_value(c: Card) -> int:
    """2 to 14, ace high."""
    return _RANK_VALUE[c["rank"]]


def same_card(a: Card, b: Card) -> bool:
    """Same rank and suit."""
    return bool(a["rank"] == b["rank"] and a["suit"] == b["suit"])


def card_key(rank: str, suit: str) -> str:
    """The card's short key: 10♦ → `10d`, Q♥ → `qh`."""
    return rank.lower() + suit[0]


def card_id(c: Card) -> str:
    """The card's short key."""
    return card_key(c["rank"], c["suit"])


def is_card(x: object) -> bool:
    """A real card: a known rank and suit."""
    if not isinstance(x, dict):
        return False
    rank, suit = x.get("rank"), x.get("suit")
    return isinstance(rank, str) and rank in RANKS and isinstance(suit, str) and suit in SUITS


def deck() -> list[Card]:
    """The 52 cards in a fixed order."""
    return [{"rank": rank, "suit": suit} for suit in SUITS for rank in RANKS]


def display_sort(cards: Sequence[Card]) -> list[Card]:
    """Suits in display order, low to high within each."""
    return sorted(cards, key=lambda c: (_DISPLAY[c["suit"]], _RANK_VALUE[c["rank"]]))


def _draw_key(c: Card) -> tuple[int, int]:
    # Higher first: by rank, then spades, hearts, diamonds, clubs.
    return (-_RANK_VALUE[c["rank"]], -_SUIT_RANK[c["suit"]])


def draw_winner(draw: Sequence[Card]) -> Seat:
    """Who drew the highest card for deal."""
    best = sorted(draw, key=_draw_key)[0]
    return next(i for i, c in enumerate(draw) if same_card(c, best))


def _lowest_key(c: Card) -> tuple[int, int, int]:
    # Spades count above every other suit, since they are trump; then by rank, then bridge order.
    trump = 1 if c["suit"] == "spades" else 0
    return (trump, _RANK_VALUE[c["rank"]], _SUIT_RANK[c["suit"]])


def lowest_card(cards: Sequence[Card]) -> Card:
    """The lowest card, spades above every other suit."""
    return sorted(cards, key=_lowest_key)[0]


def distinct_cards(x: Any, n: int) -> bool:
    """`n` distinct real cards."""
    if not isinstance(x, list) or len(x) != n or not all(is_card(c) for c in x):
        return False
    return len({card_id(c) for c in x}) == n


def is_deal(x: Any) -> bool:
    """Four hands of 13 that together are the whole deck."""
    return (
        isinstance(x, list)
        and len(x) == 4
        and all(isinstance(h, list) and len(h) == TRICKS for h in x)
        and distinct_cards([c for h in x for c in h], 52)
    )
