"""Parity with the app: every Spades table replayed step by step, plus scoring, legality and upload cases."""

import json
from typing import Any

import pytest

from app.domain.spades import (
    ApplyCtx,
    allowed_bids,
    apply_action,
    bid_problem,
    card_id,
    draw_winner,
    end_result,
    game_over,
    hand_lines,
    legal_plays,
    min_bid_for,
    new_table,
    play_problem,
    rematch_table,
    score_hand,
    set_rules,
    sit,
    timeout_play,
    totals,
    trick_winner,
    validate,
    view_for,
)
from tests.domain.golden import load, names

CASE_TABLES = ("spades-scoring", "spades-legal", "spades-validate")
GAMES = [n for n in names("spades-") if n not in CASE_TABLES]
VIEWERS = (0, 1, 2, 3, None)
_SUMMARY = ("rev", "status", "phase", "turn", "deadline")


def test_the_fixture_set_is_present() -> None:
    assert len(GAMES) == 15
    assert all(names(n) == [n] for n in CASE_TABLES)


Table = tuple[dict[str, Any], list[list[dict[str, Any]]] | None]


def _step(table: Table, step: dict[str, Any]) -> tuple[Table, dict[str, Any]]:
    state, hands = table
    at = step["at"]
    if "sit" in step:
        r = sit(state, step["sit"]["who"], step["sit"]["seat"], at)
        return (r.state, hands), ({"ok": True} if r.ok else {"ok": False, "error": r.error})
    if "setRules" in step:
        x = step["setRules"]
        r = set_rules(state, x["actor"], x["rules"], x["name"], at)
        return (r.state, hands), ({"ok": True} if r.ok else {"ok": False, "error": r.error})
    if "rematch" in step:
        x = step["rematch"]
        fresh = rematch_table(state, id=x["id"], code=x["code"], now=at, swap=x["swap"])
        return (fresh, None), {"ok": True}
    c = step.get("ctx", {})
    ctx = ApplyCtx(
        now=at,
        hands=hands,
        draw=c.get("draw"),
        deal=c.get("deal"),
        rev=c.get("rev"),
        last_seen_at=c.get("lastSeenAt"),
    )
    a = apply_action(state, step["action"], step["actor"], ctx)
    got = {"ok": True, "noop": a.noop} if a.ok else {"ok": False, "error": a.error}
    return (a.state, a.hands), got


def _views(table: Table, now: int) -> list[dict[str, Any]]:
    out = []
    for v in VIEWERS:
        view = view_for(table[0], table[1], v, now)
        out.append({"me": view["me"], "blind": view["table"]["blind"]})
    return out


def _check_privacy(table: Table, now: int, where: str) -> None:
    """A viewer gets its own hand at most, and no card still held by another seat."""
    state, hands = table
    for v in VIEWERS:
        view = view_for(state, hands, v, now)
        if view["me"]["hand"] is not None:
            assert hands is not None and v is not None, where
            assert view["me"]["hand"] == hands[v], where
        # The draw for deal and finished hands are public; anything else must not name a held card.
        public = {k: x for k, x in view["table"].items() if k not in ("drawn", "history")}
        text = json.dumps({"table": public, "me": view["me"]})
        for other in range(4):
            if hands is None or other == v:
                continue
            for c in hands[other]:
                assert json.dumps(c) not in text, f"{where}: viewer {v} sees seat {other}'s {c}"


@pytest.mark.parametrize("name", GAMES)
def test_replay_matches_the_app(name: str) -> None:
    fixture = load(name)
    table: Table = (new_table(**fixture["setup"]), None)

    for i, step in enumerate(fixture["steps"]):
        table, got = _step(table, step)
        expect = dict(step["expect"])
        views = expect.pop("views", None)
        got |= {k: table[0][k] for k in _SUMMARY}
        assert got == expect, f"step {i}"
        if views is not None:
            assert _views(table, step["at"]) == views, f"step {i} views"
        _check_privacy(table, step["at"], f"step {i}")

    expect = fixture["expect"]
    last = fixture["steps"][-1]["at"]
    state, hands = table
    assert state == expect["state"]
    assert hands == expect["hands"]
    assert _views(table, last) == expect["views"]
    assert totals(state["history"]) == expect["totals"]
    assert hand_lines(state["history"]) == expect["lines"]


def test_scoring_matches_the_app() -> None:
    fixture = load("spades-scoring")
    for case in fixture["hands"]:
        assert score_hand(case["hand"], case["bagsBefore"]) == case["expect"], case["name"]
    for run in fixture["runs"]:
        assert hand_lines(run["hands"]) == run["expect"]["lines"], run["name"]
        assert totals(run["hands"]) == run["expect"]["totals"], run["name"]
    for case in fixture["gameOver"]:
        assert game_over(case["scores"], case["rules"]) == case["expect"], case["name"]
    for case in fixture["endResult"]:
        assert end_result(case["scores"]) == case["expect"], case["scores"]


def test_legality_matches_the_app() -> None:
    fixture = load("spades-legal")
    outside = {"rank": "Q", "suit": "hearts"}
    for case in fixture["plays"]:
        hand, trick, broken = case["hand"], case["trick"], case["broken"]
        assert legal_plays(hand, trick, broken) == case["expect"]["legal"], case["name"]
        problems = {card_id(c): play_problem(c, hand, trick, broken) for c in [*hand, outside]}
        assert problems == case["expect"]["problems"], case["name"]
    for t in fixture["tricks"]:
        assert trick_winner(t["plays"]) == t["winner"]
    for case in fixture["timeoutPlay"]:
        assert timeout_play(case["hand"], case["trick"], case["broken"]) == case["expect"]
    for d in fixture["draws"]:
        assert draw_winner(d["draw"]) == d["dealer"]


def test_bids_match_the_app() -> None:
    fixture = load("spades-legal")
    assert fixture["bids"]
    for case in fixture["bids"]:
        seat, bids, rules, expect = case["seat"], case["bids"], case["rules"], case["expect"]
        where = f"{case['name']} seat {seat} {bids}"
        assert min_bid_for(seat, bids, rules) == expect["min"], where
        assert allowed_bids(seat, bids, rules) == expect["allowed"], where
        # Keys are JavaScript's String(bid): "-1", "0", "2.5".
        problems = {k: bid_problem(json.loads(k), seat, bids, rules) for k in expect["problems"]}
        assert problems == expect["problems"], where


def test_upload_checks_match_the_app() -> None:
    fixture = load("spades-validate")
    assert len(fixture["cases"]) == 28
    for case in fixture["cases"]:
        assert validate(case["game"], fixture["now"]) == case["expect"], case["name"]
