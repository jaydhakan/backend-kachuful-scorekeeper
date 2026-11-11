"""Decks, the cards taken out for an even deal, card points and the rules check (app: cards.ts)."""

from typing import Any

from app.domain.kaali_teeri.game import KtCard, KtRules, includes, same
from app.domain.rules import is_int

SUITS = ("spades", "hearts", "diamonds", "clubs")
RANKS = ("A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2")
TEN_POINT_RANKS = ("A", "K", "Q", "J", "10")
MIN_PLAYERS = 4
MAX_PLAYERS = 10
DECK_CARDS = 52
POINTS_PER_DECK = 250
STEPS = (5, 10)
LENGTHS = ("endless", "to1000")

# Zero-point cards in the order they come out to make the deal even.
REMOVAL_ORDER: tuple[KtCard, ...] = tuple(
    KtCard(rank=rank, suit=suit)
    for rank, suit in (
        ("2", "clubs"),
        ("2", "diamonds"),
        ("2", "hearts"),
        ("2", "spades"),
        ("3", "clubs"),
        ("3", "diamonds"),
        ("3", "hearts"),
        ("4", "clubs"),
        ("4", "diamonds"),
        ("4", "hearts"),
        ("4", "spades"),
    )
)


def decks_for(n: int) -> int:
    """1 deck up to 6 players, 2 from 7."""
    return 1 if n <= 6 else 2


def partners_for(n: int) -> int:
    """4p 1 · 5p 1 · 6p 2 · 7p 2 · 8p 3 · 9p 3 · 10p 4."""
    return n // 2 - 1


def deck_total(decks: int) -> int:
    """250 points per deck."""
    return POINTS_PER_DECK * decks


def card_points(card: KtCard) -> int:
    """3♠ 30, A K Q J 10 ten each, every 5 five, the rest nothing."""
    if same(card.rank, "3") and same(card.suit, "spades"):
        return 30
    if same(card.rank, "5"):
        return 5
    return 10 if includes(TEN_POINT_RANKS, card.rank) else 0


def take_out(count: int, decks: int) -> list[KtCard]:
    """The first `count` cards out; with two decks both copies of a card go before the next."""
    return [c for c in REMOVAL_ORDER for _ in range(decks)][:count]


def removed_cards(n: int) -> list[KtCard]:
    """One entry per physical card taken out, so a card out of both decks appears twice."""
    decks = decks_for(n)
    return take_out((DECK_CARDS * decks) % n, decks)


def cards_each(n: int) -> int:
    """Cards dealt to each player."""
    return (DECK_CARDS * decks_for(n)) // n


def same_card(a: KtCard, b: KtCard) -> bool:
    """Same rank and suit, whatever the copy."""
    return same(a.rank, b.rank) and same(a.suit, b.suit)


def copies_in_play(card: KtCard, n: int) -> int:
    """How many copies of the card are dealt at this table."""
    return decks_for(n) - sum(1 for c in removed_cards(n) if same_card(c, card))


def default_rules(n: int) -> KtRules:
    """The rules a new game of `n` players starts with."""
    decks = decks_for(n)
    return KtRules(
        decks=decks,
        partners=partners_for(n),
        min_bid=150 if decks == 1 else 350,
        step=5,
        length="endless",
    )


def min_bid_range(decks: int) -> tuple[int, int]:
    """Advanced: the minimum bid goes from half the deck + 5 up to the deck - 5."""
    return deck_total(decks) // 2 + 5, deck_total(decks) - 5


def _min_bid_ok(min_bid: Any, step: Any, decks: int) -> bool:
    lo, hi = min_bid_range(decks)
    return is_int(min_bid) and lo <= min_bid <= hi and min_bid % step == 0


def rules_ok(r: KtRules, n: int) -> bool:
    """Whether a game's rules fit its table; the minimum sits on the step, so 250/500 is biddable."""
    decks = decks_for(n)
    return (
        same(r.decks, decks)
        and same(r.partners, partners_for(n))
        and includes(STEPS, r.step)
        and _min_bid_ok(r.min_bid, r.step, decks)
        and includes(LENGTHS, r.length)
    )
