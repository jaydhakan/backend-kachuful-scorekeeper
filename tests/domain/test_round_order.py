"""Round order (app: cards.ts): up first deals 3 → max → 3, down first deals max → 3 → max."""

from app.domain.game import game_from_payload
from app.domain.rules import cards_for
from app.domain.validate import validate
from tests.game_builder import make_payload

START = 1_790_000_000_000
NOW = START + 10_000_000


def test_up_first_deals_three_to_max_and_back() -> None:
    assert [cards_for(i, 8) for i in range(13)] == [3, 4, 5, 6, 7, 8, 8, 7, 6, 5, 4, 3, 3]


def test_down_first_deals_max_to_three_and_back() -> None:
    cards = [cards_for(i, 8, "down") for i in range(13)]

    assert cards == [8, 7, 6, 5, 4, 3, 3, 4, 5, 6, 7, 8, 8]


def test_a_game_without_order_is_dealt_up_first() -> None:
    payload = make_payload(["a", "b", "c", "d"], rounds=3, start_ms=START)
    payload.pop("order", None)

    game = game_from_payload(payload)

    assert game.order == "up"
    assert validate(game, NOW).ok


def test_a_down_first_game_validates() -> None:
    payload = make_payload(["a", "b", "c", "d"], rounds=14, start_ms=START, order="down")

    assert [r["cards"] for r in payload["rounds"]][:3] == [10, 9, 8]
    assert validate(game_from_payload(payload), NOW).ok


def test_a_down_first_game_dealt_up_first_fails_the_round_plan() -> None:
    payload = make_payload(["a", "b", "c", "d"], rounds=3, start_ms=START)
    payload["order"] = "down"

    result = validate(game_from_payload(payload), NOW)

    assert (result.ok, result.reason, result.round_index) == (False, "round_plan", 0)
