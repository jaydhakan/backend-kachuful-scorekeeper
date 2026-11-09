"""Which partner cards may be called and who may have played them (app: calls.ts)."""

from collections.abc import Sequence

from app.domain.kaali_teeri.cards import RANKS, SUITS, copies_in_play, partners_for, same_card
from app.domain.kaali_teeri.game import MISSING, KtCard, KtHand, includes, same

COPIES = (1, 2, "other")


def copy_needed(card: KtCard, n: int) -> bool:
    """A called card names its copy only when both copies are in play."""
    return copies_in_play(card, n) == 2


def _callable(card: KtCard, n: int) -> bool:
    if not includes(RANKS, card.rank) or not includes(SUITS, card.suit):
        return False
    left = copies_in_play(card, n)
    if left == 0:
        return False
    return includes(COPIES, card.copy) if left == 2 else card.copy is MISSING


def _clash(a: KtCard, b: KtCard) -> bool:
    """Two calls of one card clash when they name the same copy, or when either is "the other one"."""
    return same_card(a, b) and (
        same(a.copy, b.copy) or same(a.copy, "other") or same(b.copy, "other")
    )


def can_add(card: KtCard, called: Sequence[KtCard], n: int) -> bool:
    """In play, marked right, no clash, and a partner left to call."""
    return (
        len(called) < partners_for(n)
        and _callable(card, n)
        and not any(_clash(c, card) for c in called)
    )


def valid_called(called: Sequence[KtCard], n: int) -> bool:
    """Exactly one card per partner, each addable in turn."""
    return len(called) == partners_for(n) and all(
        can_add(c, called[:i], n) for i, c in enumerate(called)
    )


def _played_ok(hand: KtHand, i: int, player: object, ids: Sequence[str]) -> bool:
    if player is None:
        return True
    other = same(hand.called[i].copy, "other")
    return includes(ids, player) and not (other and same(player, hand.bidder))


def valid_played_by(hand: KtHand, ids: Sequence[str]) -> bool:
    """One entry per called card: a seat or null; "the other one" is never the bidder's."""
    return len(hand.played_by) == len(hand.called) and all(
        _played_ok(hand, i, p, ids) for i, p in enumerate(hand.played_by)
    )
