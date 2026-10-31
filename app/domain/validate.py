"""Upload validation (plan §7.2; app: src/domain/validate.ts). Each failure has a reason code."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.game import Game, Round
from app.domain.rules import (
    TRUMPS,
    cards_for,
    fits,
    is_int,
    max_for,
    player_counts,
    total_rounds,
)

FIVE_MIN_MS = 5 * 60_000
TEN_MIN_MS = 10 * 60_000
NAME_MAX = 14


class InvalidReason(StrEnum):
    """`invalid_game` reasons."""

    PLAYERS = "players"
    MAX_CARDS = "max_cards"
    ROUND_PLAN = "round_plan"
    ROUND_ORDER = "round_order"
    BIDS = "bids"
    RESULTS = "results"
    TRICKS = "tricks"
    TIMES = "times"


@dataclass(frozen=True)
class Validity:
    """`ok`, or the first reason found and the round it concerns."""

    ok: bool
    reason: InvalidReason | None = None
    round_index: int | None = None


_OK = Validity(ok=True)


def js_length(text: str) -> int:
    """String length as JavaScript counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _bad(reason: InvalidReason, round_index: int | None = None) -> Validity:
    return Validity(ok=False, reason=reason, round_index=round_index)


def _players_ok(game: Game) -> bool:
    n = len(game.players)
    ids = [p.id for p in game.players]
    registry_ids = [p.player_id for p in game.players if p.player_id]
    return (
        n in player_counts(game.decks)
        and len(set(ids)) == n
        and len(set(registry_ids)) == len(registry_ids)
        and all(p.name.strip() and js_length(p.name) <= NAME_MAX for p in game.players)
    )


def _round_plan_error(game: Game) -> Validity | None:
    n = len(game.players)
    for i, r in enumerate(game.rounds):
        dealer = (game.first_dealer_index + i) % n
        planned = (i, cards_for(i, game.max_cards), TRUMPS[i % 4], dealer, (dealer + 1) % n)
        actual = (r.index, r.cards, r.trump, r.dealer_index, r.first_player_index)
        if any(a != p or isinstance(a, bool) for a, p in zip(actual, planned, strict=True)):
            return _bad(InvalidReason.ROUND_PLAN, i)
    return None


def _round_order_ok(game: Game) -> bool:
    last = len(game.rounds) - 1
    if any(i < last and not r.done for i, r in enumerate(game.rounds)):
        return False
    if len(game.rounds) > total_rounds(game):
        return False
    return not (game.status == "finished" and (last < 0 or not game.rounds[last].done))


def _done_round_error(r: Round, ids: list[str]) -> Validity | None:
    bids: list[Any] = [r.bids.get(pid) for pid in ids]
    if any(not is_int(b) or b < 0 or b > r.cards for b in bids):
        return _bad(InvalidReason.BIDS, r.index)
    if sum(bids) == r.cards:
        return _bad(InvalidReason.BIDS, r.index)
    if any(r.results.get(pid) not in ("made", "missed") for pid in ids):
        return _bad(InvalidReason.RESULTS, r.index)
    if r.tricks is not None:
        tricks: list[Any] = [r.tricks.get(pid) for pid in ids]
        if (
            any(not is_int(t) or t < 0 or t > r.cards for t in tricks)
            or sum(tricks) != r.cards
            or any(
                (r.results.get(pid) == "made") != (r.tricks.get(pid) == r.bids.get(pid))
                for pid in ids
            )
        ):
            return _bad(InvalidReason.TRICKS, r.index)
    elif not fits(r.cards, ids, r.bids, r.results):
        return _bad(InvalidReason.RESULTS, r.index)
    return None


def _times_ok(game: Game, now_ms: int) -> bool:
    times = [game.created_at, game.updated_at, game.finished_at] + [r.done_at for r in game.rounds]
    if any(t is not None and t > now_ms + TEN_MIN_MS for t in times):
        return False
    return not any(
        r.done_at is not None
        and (r.done_at < game.created_at or r.done_at > game.updated_at + FIVE_MIN_MS)
        for r in game.rounds
    )


def validate(game: Game, now_ms: int) -> Validity:
    """Whether a game would be accepted; `now_ms` is the server's clock."""
    if not _players_ok(game):
        return _bad(InvalidReason.PLAYERS)
    if game.max_cards != max_for(len(game.players), int(game.decks)):
        return _bad(InvalidReason.MAX_CARDS)
    plan_error = _round_plan_error(game)
    if plan_error is not None:
        return plan_error
    if not _round_order_ok(game):
        return _bad(InvalidReason.ROUND_ORDER)
    ids = [p.id for p in game.players]
    for r in game.done_rounds:
        error = _done_round_error(r, ids)
        if error is not None:
            return error
    if not _times_ok(game, now_ms):
        return _bad(InvalidReason.TIMES)
    return _OK
