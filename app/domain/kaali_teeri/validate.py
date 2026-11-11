"""Upload validation (app: validate.ts): the first thing wrong with a game, and the hand it concerns."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.kaali_teeri.auction import (
    AuctionState,
    auction,
    dealer_for,
    speaking_order,
    valid_bid,
)
from app.domain.kaali_teeri.calls import can_add, valid_called, valid_played_by
from app.domain.kaali_teeri.cards import MAX_PLAYERS, MIN_PLAYERS, SUITS, deck_total, rules_ok
from app.domain.kaali_teeri.game import MISSING, KtGame, KtHand, KtRules, includes, js_number, same
from app.domain.kaali_teeri.scoring import TARGET, totals
from app.domain.rules import is_int
from app.domain.validate import js_length

PHASES = ("bidding", "calling", "playing", "result", "done")
CALLING, PLAYING, RESULT, DONE = 1, 2, 3, 4
MODES = ("quick", "auction")
COUNTED_BY = ("defenders", "bidders")
DIRECTIONS = ("cw", "ccw")
NAME_MAX = 14
FIVE_MIN_MS = 5 * 60_000
TEN_MIN_MS = 10 * 60_000


class KtInvalidReason(StrEnum):
    """`invalid_game` reasons for Kaali Teeri."""

    PLAYERS = "players"
    RULES = "rules"
    DEALER = "dealer"
    HANDS = "hands"
    AUCTION = "auction"
    BID = "bid"
    TRUMP = "trump"
    CALLED = "called"
    PLAYED_BY = "played_by"
    TEAM_POINTS = "team_points"
    TIMES = "times"


@dataclass(frozen=True)
class KtInvalid:
    """The reason, and the hand it concerns (None when it is about the whole game)."""

    reason: KtInvalidReason
    hand_index: int | None


def _players_ok(game: KtGame) -> bool:
    n = len(game.players)
    ids = [p.id for p in game.players]
    registry = [p.player_id for p in game.players if p.player_id]
    return (
        MIN_PLAYERS <= n <= MAX_PLAYERS
        and len(set(ids)) == n
        and len(set(registry)) == len(registry)
        and all(p.name.strip() and js_length(p.name) <= NAME_MAX for p in game.players)
    )


def _dealer_ok(game: KtGame) -> bool:
    first = game.first_dealer_index
    return is_int(first) and 0 <= first < len(game.players) and includes(DIRECTIONS, game.direction)


def _scored(hand: KtHand) -> bool:
    """Numbers where scoring needs them; JS would coerce junk, but the hand fails a later check anyway."""
    return all(is_int(v) or v is None or v is MISSING for v in (hand.bid, hand.team_points))


def _reaches_target(game: KtGame, upto: int) -> bool:
    hands = game.hands[: upto + 1]
    if not all(_scored(h) for h in hands):
        return False
    return any(t >= TARGET for t in totals(game, hands).values())


def _hands_problem(game: KtGame) -> KtInvalid | None:
    last = len(game.hands) - 1
    over = -1
    for i, hand in enumerate(game.hands):
        if not same(hand.dealer_index, dealer_for(game, i)):
            return KtInvalid(KtInvalidReason.DEALER, i)
        if (
            not same(hand.index, i)
            or not includes(PHASES, hand.phase)
            or (i < last and not hand.done)
            or (i == last and game.status == "finished" and not hand.done)
            or over >= 0
        ):
            return KtInvalid(KtInvalidReason.HANDS, i)
        if hand.done and same(game.rules.length, "to1000") and _reaches_target(game, i):
            over = i
    return None


def _auction_problem(
    game: KtGame, hand: KtHand, i: int, at: int
) -> tuple[bool, AuctionState | None]:
    """(bad, the auction's end state when the hand was bid turn by turn)."""
    if not includes(MODES, hand.mode):
        return True, None
    if not same(hand.mode, "auction"):
        return len(hand.calls) > 0, None
    state = auction(hand.calls, speaking_order(game, i), game.rules)
    return state.invalid_at is not None or (at >= CALLING and not state.done), state


def _bid_bad(
    hand: KtHand, at: int, ids: Sequence[str], r: KtRules, state: AuctionState | None
) -> bool:
    if at < CALLING:
        return hand.bidder is not None or hand.bid is not None
    return (
        hand.bidder is None
        or not includes(ids, hand.bidder)
        or hand.bid is None
        or not valid_bid(hand.bid, r)
        or (
            state is not None
            and (not same(state.holder, hand.bidder) or not same(state.high, hand.bid))
        )
    )


def _trump_bad(hand: KtHand, at: int) -> bool:
    if at < PLAYING:
        return hand.trump is not None and (at < CALLING or not includes(SUITS, hand.trump))
    return not includes(SUITS, hand.trump)


def _called_bad(hand: KtHand, at: int, n: int) -> bool:
    if at < CALLING:
        return len(hand.called) > 0
    if at == CALLING:
        return not all(can_add(c, hand.called[:k], n) for k, c in enumerate(hand.called))
    return not valid_called(hand.called, n)


def _points_bad(hand: KtHand, at: int, r: KtRules) -> bool:
    pts, by = hand.team_points, hand.counted_by
    pts_ok = is_int(pts) and pts % 5 == 0 and 0 <= pts <= deck_total(int(r.decks))
    by_ok = includes(COUNTED_BY, by)
    if at < RESULT:
        return pts is not None or by is not None
    if at == RESULT:
        return (pts is not None and not pts_ok) or (by is not None and not by_ok)
    return not pts_ok or not by_ok


def _hand_problem(game: KtGame, hand: KtHand, i: int, ids: Sequence[str]) -> KtInvalidReason | None:
    """What the hand's phase says must be filled in, and that what is there is legal."""
    n, r, at = len(ids), game.rules, PHASES.index(hand.phase)
    bad_auction, state = _auction_problem(game, hand, i, at)
    if bad_auction:
        return KtInvalidReason.AUCTION
    if _bid_bad(hand, at, ids, r, state):
        return KtInvalidReason.BID
    if _trump_bad(hand, at):
        return KtInvalidReason.TRUMP
    if _called_bad(hand, at, n):
        return KtInvalidReason.CALLED
    if not valid_played_by(hand, ids) or (at == DONE and any(p is None for p in hand.played_by)):
        return KtInvalidReason.PLAYED_BY
    if _points_bad(hand, at, r):
        return KtInvalidReason.TEAM_POINTS
    return None


def _times_problem(game: KtGame, now_ms: int) -> KtInvalid | None:
    # As in JS, a null stamp compares as 0: never too late, but a null doneAt is before createdAt.
    done_ats = [js_number(h.done_at) for h in game.hands if h.done_at is not MISSING]
    stamps = [game.created_at, game.updated_at, js_number(game.finished_at), *done_ats]
    if any(t > now_ms + TEN_MIN_MS for t in stamps):
        return KtInvalid(KtInvalidReason.TIMES, None)
    for hand in game.hands:
        if hand.done_at is MISSING:
            continue
        at = js_number(hand.done_at)
        if at < game.created_at or at > game.updated_at + FIVE_MIN_MS:
            return KtInvalid(KtInvalidReason.TIMES, int(hand.index))
    return None


def validate(game: KtGame, now_ms: int) -> KtInvalid | None:
    """Whether a game would be accepted for upload, or the first thing wrong with it."""
    if not _players_ok(game):
        return KtInvalid(KtInvalidReason.PLAYERS, None)
    if not rules_ok(game.rules, len(game.players)):
        return KtInvalid(KtInvalidReason.RULES, None)
    if not _dealer_ok(game):
        return KtInvalid(KtInvalidReason.DEALER, None)
    problem = _hands_problem(game)
    if problem is not None:
        return problem
    ids = [p.id for p in game.players]
    for i, hand in enumerate(game.hands):
        reason = _hand_problem(game, hand, i, ids)
        if reason is not None:
            return KtInvalid(reason, i)
    return _times_problem(game, now_ms)
