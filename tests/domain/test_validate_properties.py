"""Property tests (plan §12.2): random valid games validate; one corrupted field gives its reason."""

import copy
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.game import game_from_payload
from app.domain.rules import max_for
from app.domain.validate import validate
from tests.game_builder import make_payload

START = 1_790_000_000_000
NOW = START + 10_000_000


@st.composite
def games(draw: st.DrawFn) -> dict[str, Any]:
    """A random valid game."""
    decks = draw(st.sampled_from([1, 2]))
    n = draw(st.integers(3, 7 if decks == 1 else 10))
    return make_payload(
        [f"x{k}" for k in range(n)],
        rounds=draw(st.integers(1, 30)),
        finished=draw(st.booleans()),
        start_ms=START,
        seed=draw(st.integers(0, 10_000)),
        decks=decks,
        first_dealer=draw(st.integers(0, n - 1)),
        length_mode=draw(st.sampled_from(["endless", "1cycle", "2cycles"])),
    )


def _reason(payload: dict[str, Any]) -> tuple[str | None, int | None]:
    result = validate(game_from_payload(payload), NOW)
    return (None if result.reason is None else result.reason.value), result.round_index


@settings(max_examples=150, deadline=None)
@given(games())
def test_random_valid_games_validate(payload: dict[str, Any]) -> None:
    assert _reason(payload) == (None, None)


@settings(max_examples=60, deadline=None)
@given(games(), st.data())
def test_one_corrupted_field_gives_its_reason(payload: dict[str, Any], data: st.DataObject) -> None:
    n = len(payload["players"])
    last = len(payload["rounds"]) - 1
    k = data.draw(st.integers(0, last))
    corruptions = {
        "players": lambda g: g["players"][0].update(name=""),
        "max_cards": lambda g: g.update(maxCards=max_for(n, g["decks"]) - 1),
        "round_plan": lambda g: g["rounds"][k].update(
            dealerIndex=(g["rounds"][k]["dealerIndex"] + 1) % n
        ),
        "bids": lambda g: g["rounds"][k]["bids"].update(p0=-1),
        "results": lambda g: g["rounds"][k]["results"].update(p0="maybe"),
        "times": lambda g: g["rounds"][k].update(doneAt=g["createdAt"] - 1),
    }
    reason = data.draw(st.sampled_from(sorted(corruptions)))
    broken = copy.deepcopy(payload)
    corruptions[reason](broken)

    found, index = _reason(broken)

    assert found == reason
    if reason in ("round_plan", "bids", "results"):
        assert index == k


def test_an_unfinished_middle_round_breaks_round_order() -> None:
    payload = make_payload(["a", "b", "c"], rounds=4, start_ms=START)
    payload["rounds"][1]["phase"] = "bidding"

    assert _reason(payload) == ("round_order", None)


def test_bids_adding_up_to_the_cards_break_the_dealers_hook() -> None:
    payload = make_payload(["a", "b", "c"], rounds=1, start_ms=START)
    payload["rounds"][0]["bids"] = {"p0": 1, "p1": 1, "p2": 1}

    assert _reason(payload) == ("bids", 0)


def test_rounds_beyond_the_length_mode_break_round_order() -> None:
    payload = make_payload(["a", "b", "c"], rounds=16, start_ms=START, length_mode="1cycle")
    extra = make_payload(["a", "b", "c"], rounds=17, start_ms=START)["rounds"][16]
    payload["rounds"].append(extra)
    payload["finishedAt"] = payload["updatedAt"] = extra["doneAt"]

    assert _reason(payload) == ("round_order", None)
