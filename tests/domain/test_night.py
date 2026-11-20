"""The night's story against the app's golden fixture, plus linking nights and the Sitara."""

from typing import Any

import pytest

from app.domain.derive import RoundFact, build_facts
from app.domain.game import game_from_payload
from app.domain.night import (
    NIGHT_FRESH_MS,
    NIGHT_GAP_MS,
    LinkGame,
    NightGame,
    NightSeat,
    link_nights,
    night_dict,
    night_id_seed,
    night_of,
    night_story,
)
from tests.domain.golden import load

H = 3600 * 1000
FIXTURE = load("night-cases")


def _without_edges(night: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    players = [{k: v for k, v in p.items() if k != "edge"} for p in night["players"]]
    return {**night, "players": players}, [p["edge"] for p in night["players"]]


@pytest.mark.parametrize("name", sorted(FIXTURE["cases"]))
def test_night_matches_the_app(name: str) -> None:
    case = FIXTURE["cases"][name]
    games = [game_from_payload(p) for p in case["games"]]
    night = night_of(build_facts(games, case["now"]), case["now"])
    if case["night"] is None:
        assert night is None
        return
    assert night is not None
    got, got_edges = _without_edges(night_dict(night))
    want, want_edges = _without_edges(case["night"])
    assert got == want
    assert got_edges == pytest.approx(want_edges, abs=1e-9)


def test_night_constants_match_the_app() -> None:
    assert FIXTURE["gapMs"] == NIGHT_GAP_MS
    assert FIXTURE["freshMs"] == NIGHT_FRESH_MS


def _link(game_id: str, start_h: float, end_h: float, *players: str) -> LinkGame:
    return LinkGame(game_id, int(start_h * H), int(end_h * H), frozenset(players))


def _ids(groups: list[list[LinkGame]]) -> list[list[str]]:
    return [[g.game_id for g in group] for group in groups]


def test_link_nights_chains_sequential_games() -> None:
    games = [_link("b", 1, 2, "riya"), _link("a", 0, 1, "riya"), _link("c", 4.5, 5, "riya")]
    assert _ids(link_nights(games)) == [["a", "b", "c"]]


def test_link_nights_splits_on_a_gap_past_three_hours() -> None:
    games = [_link("a", 0, 1, "riya"), _link("b", 4.01, 5, "riya")]
    assert _ids(link_nights(games)) == [["a"], ["b"]]


def test_link_nights_keeps_tables_without_a_shared_player_apart() -> None:
    games = [_link("a", 0, 1, "riya", "dev"), _link("b", 0.5, 1.5, "asha", "kabir")]
    assert _ids(link_nights(games)) == [["a"], ["b"]]


def test_link_nights_bridges_tables_through_a_shared_player() -> None:
    games = [
        _link("a", 0, 1, "riya", "dev"),
        _link("b", 0, 1.2, "asha", "kabir"),
        _link("c", 2, 3, "dev", "asha"),
    ]
    assert _ids(link_nights(games)) == [["a", "b", "c"]]


def test_link_nights_links_overlapping_games() -> None:
    games = [_link("a", 0, 10, "riya"), _link("b", 1, 2, "riya")]
    assert _ids(link_nights(games)) == [["b", "a"]]


def test_link_nights_links_to_an_earlier_game_that_is_not_the_latest() -> None:
    # y starts more than 3h after x ends, so a chain of consecutive ends would leave x alone;
    # c started within 3h of x's end, which links all three.
    games = [_link("x", 0, 6, "riya"), _link("y", 9.5, 20, "riya"), _link("c", 8, 21, "riya")]
    assert _ids(link_nights(games)) == [["x", "y", "c"]]


def test_night_id_seed_is_the_earliest_created_game() -> None:
    games = [_link("b", 0, 2, "riya"), _link("a", 0, 3, "riya"), _link("c", -1, 4, "dev")]
    assert night_id_seed(games) == "c"
    assert night_id_seed(games[:2]) == "a"


def _round(player_id: str, points: int, bid: int, outcome: str) -> RoundFact:
    return RoundFact(
        game_id="k1",
        round_index=0,
        player_id=player_id,
        done_at=0,
        cards=3,
        max_cards=3,
        trump="S",
        bid=bid,
        outcome=outcome,
        points=points,
        is_dealer=False,
        hook_hits=0,
        run_hot=0,
        run_cold=0,
        table_size=3,
        made_count=1,
    )


def test_night_story_counts_other_kinds_without_edge() -> None:
    kachuful = NightGame(
        "k1",
        "kachuful",
        0,
        H,
        (NightSeat("riya", True, 30), NightSeat("dev", False, 0), NightSeat("asha", False, 0)),
    )
    teeri = NightGame(
        "t1", "kaali-teeri", 2 * H, 3 * H, (NightSeat("dev", True), NightSeat("asha", True))
    )
    rounds = [_round("riya", 30, 2, "made"), _round("dev", -10, 1, "missed")]
    night = night_story([kachuful, teeri], rounds, None, [])
    by_id = {p.player_id: p for p in night.players}
    assert (by_id["dev"].games, by_id["dev"].wins, by_id["dev"].edge) == (2, 1, -10)
    assert (by_id["riya"].games, by_id["riya"].wins, by_id["riya"].edge) == (1, 1, 20)
    assert night.game_winners == (("riya",), ("dev", "asha"))
    assert night.game_ids == ("k1", "t1")
    assert (night.from_, night.to) == (0, 3 * H)
    assert night.winner == "riya"
    assert night.swing is not None and night.swing.points == 30
    assert night.brave_call is not None and night.brave_call.player_id == "dev"
