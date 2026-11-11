"""Parity with the app: every Ishaara replay step by step, its final state and rows, and the boards set."""

from typing import Any

import pytest

from app.domain.ishaara import (
    Actor,
    apply_action,
    head_to_head,
    join,
    leaderboard,
    new_game,
    player_rows,
    revealed_count,
    tallies,
)
from tests.domain.golden import load, names

GAMES = [n for n in names("ishaara-") if n != "ishaara-set-boards"]


def test_the_fixture_set_is_present() -> None:
    assert len(GAMES) == 10


def _actor(raw: dict[str, Any]) -> Actor:
    return Actor(kind=raw["kind"], team=raw.get("team"))


def _start(fixture: dict[str, Any]) -> dict[str, Any]:
    setup = fixture["setup"]
    return new_game(
        game_id=setup["id"],
        players=setup["players"],
        words=setup["words"],
        rules=setup["rules"],
        starting=setup["starting"],
        now=setup["createdAt"],
    )


def _step(
    state: dict[str, Any], key: list[str], step: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    if "join" in step:
        return join(state, step["join"], step["at"]), {"ok": True}
    r = apply_action(state, key, step["action"], _actor(step["actor"]), step["at"])
    return r.state, ({"ok": True} if r.ok else {"ok": False, "error": r.error})


@pytest.mark.parametrize("name", GAMES)
def test_replay_matches_the_app(name: str) -> None:
    fixture = load(name)
    key = fixture["setup"]["key"]
    state = _start(fixture)

    for i, step in enumerate(fixture["steps"]):
        state, got = _step(state, key, step)
        assert got == step["expect"], f"step {i}"

    expect = fixture["expect"]
    assert state == expect["state"]
    assert (player_rows(state) if state["status"] == "finished" else None) == expect["rows"]
    assert revealed_count(state) == expect["revealedCount"]


def _approx(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: pytest.approx(v) if isinstance(v, float) else v for k, v in r.items()} for r in rows
    ]


@pytest.mark.parametrize("sort", ["wins", "rate", "found"])
def test_leaderboard_matches_the_app(sort: str) -> None:
    fixture = load("ishaara-set-boards")
    assert leaderboard(fixture["rows"], sort) == _approx(fixture["expect"]["leaderboard"][sort])


def test_tallies_match_the_app() -> None:
    fixture = load("ishaara-set-boards")
    assert tallies(fixture["rows"]) == _approx(fixture["expect"]["tallies"])


def test_head_to_head_matches_the_app() -> None:
    fixture = load("ishaara-set-boards")
    pairs = fixture["expect"]["headToHead"]
    assert pairs
    for pair in pairs:
        expect = {k: v for k, v in pair.items() if k not in ("a", "b")}
        assert head_to_head(fixture["rows"], pair["a"], pair["b"]) == expect
