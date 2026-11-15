"""Spades rules the fixtures don't pin down: JavaScript value semantics on malformed input."""

import copy
from dataclasses import dataclass, replace
from typing import Any

import pytest

from app.domain.spades import (
    STANDARD_RULES,
    ApplyCtx,
    apply_action,
    deck,
    new_table,
    rules_ok,
    set_rules,
    sit,
    validate,
)
from tests.domain.golden import load

T0 = 1_794_852_000_000


def who(n: int) -> dict[str, Any]:
    """An account for seat n."""
    return {"userId": f"u-{n}", "playerId": f"p-{n}", "name": f"P{n}", "emoji": "x", "color": "c"}


@dataclass(frozen=True)
class Kit:
    """A table, its private hands and its clock."""

    state: dict[str, Any]
    hands: list[list[dict[str, Any]]] | None
    now: int


def lobby(rules: dict[str, Any] | None = None) -> Kit:
    """Four seated, nobody ready."""
    r = {**STANDARD_RULES, **(rules or {})}
    s = new_table(id="t", code="KQ7M", name="Table", rules=r, host=who(0), now=T0)
    for n in (1, 2, 3):
        s = sit(s, who(n), None, T0).state
    return Kit(state=s, hands=None, now=T0)


def act(k: Kit, actor: Any, action: Any, dt: int = 1000, **ctx: Any) -> tuple[Kit, Any]:
    """Applies one action dt ms later."""
    now = k.now + dt
    r = apply_action(k.state, action, actor, ApplyCtx(now=now, hands=k.hands, **ctx))
    return Kit(state=r.state, hands=r.hands, now=now), r


def started(rules: dict[str, Any] | None = None, deal: list[Any] | None = None) -> Kit:
    """Ready, drawn (seat 3 deals) and dealt: bidding has begun at seat 0."""
    k = lobby(rules)
    for s in range(4):
        k, _ = act(k, s, {"type": "ready"})
    draw = [{"rank": r, "suit": "hearts"} for r in ("2", "3", "4")] + [
        {"rank": "A", "suit": "spades"}
    ]
    k, _ = act(k, 0, {"type": "drawForDeal"}, draw=draw)
    cards = deal or [deck()[i * 13 : i * 13 + 13] for i in range(4)]
    k, r = act(k, 3, {"type": "deal"}, deal=cards)
    assert r.ok and k.state["phase"] == "bidding"
    return k


def test_a_whole_float_bid_counts_and_is_stored_as_an_int() -> None:
    k, r = act(started(), 0, {"type": "bid", "bid": 3.0})
    assert r.ok and not r.noop
    assert k.state["bids"][0] == 3 and isinstance(k.state["bids"][0], int)


@pytest.mark.parametrize("bid", [2.5, True, "3", None, [3], -0.5, 14])
def test_a_bid_that_isnt_a_whole_number_in_range_is_bad(bid: Any) -> None:
    _, r = act(started(), 0, {"type": "bid", "bid": bid})
    assert r.error == {"code": "invalid_action", "reason": "bad-bid"}


def test_a_missing_bid_is_bad() -> None:
    _, r = act(started(), 0, {"type": "bid"})
    assert r.error == {"code": "invalid_action", "reason": "bad-bid"}


@pytest.mark.parametrize("actor", [True, False, "0", 4, -1, 0.5, None, [0]])
def test_an_actor_that_isnt_a_seat_is_not_seated(actor: Any) -> None:
    _, r = act(started(), actor, {"type": "bid", "bid": 3})
    assert r.error == {"code": "not_your_turn"}


def test_a_whole_float_actor_is_that_seat() -> None:
    k, r = act(started(), 0.0, {"type": "bid", "bid": 3})
    assert r.ok and k.state["bids"][0] == 3


@pytest.mark.parametrize("action", [None, "bid", 3, [], {}, {"type": None}, {"type": ["bid"]}])
def test_a_malformed_action_is_refused_without_raising(action: Any) -> None:
    k = started()
    before = copy.deepcopy(k.state)
    _, r = act(k, 0, action)
    assert not r.ok and r.state is k.state and r.hands is k.hands
    assert k.state == before


@pytest.mark.parametrize(
    "card",
    [None, {}, {"rank": "1", "suit": "stars"}, {"rank": 10, "suit": "hearts"}, ["A", "spades"]],
)
def test_a_card_that_isnt_real_is_bad(card: Any) -> None:
    k = started({"nil": False})
    for b in (3, 3, 3, 3):
        k, _ = act(k, k.state["turn"], {"type": "bid", "bid": b})
    _, r = act(k, k.state["turn"], {"type": "play", "card": card})
    assert r.error == {"code": "invalid_action", "reason": "bad-card"}


def test_a_played_card_keeps_only_rank_and_suit() -> None:
    k = started()
    for b in (3, 3, 3, 3):
        k, _ = act(k, k.state["turn"], {"type": "bid", "bid": b})
    seat = k.state["turn"]
    assert k.hands is not None
    card = {**k.hands[seat][-1], "extra": 1}
    k, r = act(k, seat, {"type": "play", "card": card})
    assert r.ok and k.state["trick"][0]["card"] == {"rank": card["rank"], "suit": card["suit"]}


@pytest.mark.parametrize("seat", [9, "1", True, None, 1.5])
def test_replace_with_a_seat_that_isnt_one_is_bad(seat: Any) -> None:
    _, r = act(started(), 0, {"type": "replace", "seat": seat})
    assert r.error == {"code": "invalid_action", "reason": "bad-seat"}


@pytest.mark.parametrize("emoji", [None, 1, "x", ["\U0001f44f"], "\U0001f44f️"])
def test_only_the_six_reactions_are_accepted(emoji: Any) -> None:
    _, r = act(started(), 1, {"type": "react", "emoji": emoji})
    assert r.error == {"code": "invalid_action", "reason": "bad-emoji"}
    _, ok = act(started(), 1, {"type": "react", "emoji": "\U0001fae3"})
    assert ok.ok and ok.noop


@pytest.mark.parametrize("rid", ["", 5, None, ["x"]])
def test_a_rematch_needs_a_non_empty_string_id(rid: Any) -> None:
    k, _ = act(started(), 0, {"type": "cancelGame"})
    _, r = act(k, 0, {"type": "rematch", "id": rid})
    assert r.error == {"code": "invalid_action", "reason": "wrong-phase"}


@pytest.mark.parametrize("draw", [None, [], "abcd", [{"rank": "A", "suit": "spades"}] * 4])
def test_a_malformed_draw_is_bad(draw: Any) -> None:
    k = lobby()
    for s in range(4):
        k, _ = act(k, s, {"type": "ready"})
    _, r = act(k, 0, {"type": "drawForDeal"}, draw=draw)
    assert r.error == {"code": "invalid_action", "reason": "bad-draw"}


def test_rules_compare_numbers_as_javascript_does() -> None:
    assert rules_ok({**STANDARD_RULES, "timerSec": 60.0, "target": 300.0})
    assert not rules_ok({**STANDARD_RULES, "target": True})
    assert not rules_ok({**STANDARD_RULES, "nil": 1})
    assert not rules_ok({k: v for k, v in STANDARD_RULES.items() if k != "mercy"})
    assert not rules_ok([STANDARD_RULES])


def test_set_rules_stores_whole_floats_as_ints() -> None:
    k = lobby()
    r = set_rules(k.state, 0, {**STANDARD_RULES, "timerSec": 60.0}, None, k.now)
    assert r.ok and r.state["rules"]["timerSec"] == 60
    assert isinstance(r.state["rules"]["timerSec"], int)


def test_a_table_name_counts_utf16_units_and_is_trimmed() -> None:
    k = lobby()
    # 20 emoji are 40 UTF-16 units; one more is too long.
    assert set_rules(k.state, 0, STANDARD_RULES, " " + "\U0001f525" * 20 + "　", k.now).ok
    long = set_rules(k.state, 0, STANDARD_RULES, "\U0001f525" * 21, k.now)
    assert long.error == {"code": "invalid_action", "reason": "bad-rules"}
    assert not set_rules(k.state, 0, STANDARD_RULES, "﻿ ", k.now).ok
    assert not set_rules(k.state, 0, STANDARD_RULES, 5, k.now).ok


@pytest.mark.parametrize("seat", ["1", True, 4, 1.5])
def test_sitting_in_a_seat_that_isnt_one_is_bad(seat: Any) -> None:
    s = new_table(id="t", code="K", name="T", rules=STANDARD_RULES, host=who(0), now=T0)
    assert sit(s, who(1), seat, T0).error == {"code": "invalid_action", "reason": "bad-seat"}
    assert sit(s, who(1), 2.0, T0).state["seats"][2]["who"] == who(1)


def _won_on_target() -> dict[str, Any]:
    game: dict[str, Any] = load("spades-validate")["cases"][0]["game"]
    return game


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"players": "four"}, "players"),
        ({"players": None}, "players"),
        ({"rules": None}, "rules"),
        ({"firstDealerIndex": True}, "dealer"),
        ({"firstDealerIndex": 0.0}, None),
        ({"status": ["finished"]}, "result"),
        ({"hands": [None]}, "dealer"),
        ({"hands": "x", "status": "active", "result": None}, "result"),
        ({"createdAt": None}, "times"),
        ({"updatedAt": True}, "times"),
    ],
)
def test_malformed_uploads_get_a_reason_and_never_raise(
    change: dict[str, Any], reason: str | None
) -> None:
    got = validate({**_won_on_target(), **change}, load("spades-validate")["now"])
    assert (got or {}).get("reason") == reason


def test_a_player_name_counts_utf16_units() -> None:
    game = _won_on_target()
    now = load("spades-validate")["now"]
    seven = [{**game["players"][0], "name": "\U0001f525" * 7}, *game["players"][1:]]
    eight = [{**game["players"][0], "name": "\U0001f525" * 8}, *game["players"][1:]]
    assert validate({**game, "players": seven}, now) is None
    assert validate({**game, "players": eight}, now) == {"reason": "players", "handIndex": None}


def test_junk_hands_in_an_upload_never_raise() -> None:
    game = _won_on_target()
    now = load("spades-validate")["now"]
    junk: list[Any] = [
        {"bids": "abcd"},
        {"bids": [1, 2, 3, "x"], "teamTricks": "ab"},
        {"teamTricks": [5]},
        {"nilTricks": None, "bids": [0, 3, 3, 3]},
        {"blind": {"0": True}},
        {"phase": None},
    ]
    for change in junk:
        hands = [{**game["hands"][0], **change}, *game["hands"][1:]]
        got = validate({**game, "hands": hands}, now)
        assert got is not None and got["reason"] in ("hands", "bids", "tricks", "result")


def test_a_timeout_from_any_seat_is_refused_before_the_deadline() -> None:
    k = started()
    _, r = act(k, 2, {"type": "timeout"}, dt=10)
    assert r.error == {"code": "invalid_action", "reason": "too-early"}
    k2 = replace(k, now=k.now)
    _, stale = act(k2, 2, {"type": "timeout"}, dt=10, rev=k.state["rev"] - 1)
    assert stale.ok and stale.noop
