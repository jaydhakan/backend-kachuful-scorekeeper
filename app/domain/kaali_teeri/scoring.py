"""Teams, slots, hand scores, totals and standings (app: scoring.ts). Nobody goes negative."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.kaali_teeri.cards import deck_total
from app.domain.kaali_teeri.game import (
    MISSING,
    KtGame,
    KtHand,
    KtRules,
    KtSeat,
    Sweep,
    includes,
    same,
)

TARGET = 1000


@dataclass(frozen=True)
class HandResult:
    """Whether the bid was made, and which side (if any) swept."""

    made: bool
    sweep: Sweep


@dataclass(frozen=True)
class KtStanding:
    """A seat's place: `index` is the seat, `total` its points, `rank` competition style."""

    seat: KtSeat
    index: int
    total: int
    rank: int


def _null(value: Any) -> bool:
    """JavaScript's `== null`: null or undefined."""
    return value is None or value is MISSING


def team(hand: KtHand) -> list[Any]:
    """The bidder first, then each partner once, in the order of their first called card."""
    if _null(hand.bidder):
        return []
    out = [hand.bidder]
    for p in hand.played_by:
        if not _null(p) and not includes(out, p):
            out.append(p)
    return out


def slots(hand: KtHand) -> dict[Any, int]:
    """1 for the bidder plus 1 per called card a player played."""
    s = {p: 1 if same(p, hand.bidder) else 0 for p in team(hand)}
    for p in hand.played_by:
        if not _null(p) and not _null(hand.bidder):
            s[p] += 1
    return s


def result(hand: KtHand, rules: KtRules) -> HandResult | None:
    """Made or missed, and the sweep; None until the points are in."""
    if _null(hand.team_points) or _null(hand.bid):
        return None
    total = deck_total(int(rules.decks))
    points = hand.team_points
    if same(points, total):
        sweep = Sweep.BIDDERS
    elif same(points, 0):
        sweep = Sweep.DEFENDERS
    else:
        sweep = Sweep.NONE
    return HandResult(made=bool(points >= hand.bid), sweep=sweep)


def _player_score(res: HandResult, mine: bool, my_slots: int, bid: int, total: int) -> int:
    if res.sweep != Sweep.NONE:
        return total if mine == (res.sweep == Sweep.BIDDERS) else 0
    if res.made:
        return bid * my_slots if mine else 0
    return 0 if mine else bid


def hand_score(hand: KtHand, rules: KtRules, ids: Sequence[str]) -> dict[str, int]:
    """Each seat's points for the hand; a sweep replaces normal scoring, slots included."""
    res = result(hand, rules)
    if res is None or _null(hand.bidder):
        return dict.fromkeys(ids, 0)
    total = deck_total(int(rules.decks))
    s = slots(hand)
    return {seat: _player_score(res, seat in s, s.get(seat, 0), hand.bid, total) for seat in ids}


def totals(game: KtGame, hands: Sequence[KtHand] | None = None) -> dict[str, int]:
    """Derived from the hands (done ones by default), never stored."""
    ids = [p.id for p in game.players]
    out = dict.fromkeys(ids, 0)
    for hand in game.done_hands if hands is None else hands:
        score = hand_score(hand, game.rules, ids)
        for seat in ids:
            out[seat] += score[seat]
    return out


def standings(game: KtGame, hands: Sequence[KtHand] | None = None) -> list[KtStanding]:
    """Highest total first; ties share a rank (1, 1, 3) and keep seat order."""
    t = totals(game, hands)
    order = sorted(range(len(game.players)), key=lambda i: (-t[game.players[i].id], i))
    out: list[KtStanding] = []
    for k, i in enumerate(order):
        total = t[game.players[i].id]
        rank = out[-1].rank if out and out[-1].total == total else k + 1
        out.append(KtStanding(seat=game.players[i], index=i, total=total, rank=rank))
    return out


def game_over(game: KtGame) -> bool:
    """First to 1000 ends after the hand in which someone reaches it; endless games never do."""
    return same(game.rules.length, "to1000") and any(v >= TARGET for v in totals(game).values())
