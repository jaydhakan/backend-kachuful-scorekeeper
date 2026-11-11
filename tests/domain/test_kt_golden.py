"""Parity with the app: every Kaali Teeri fixture's validation, scores, derived rows and stats."""

import dataclasses
from typing import Any

import pytest

from app.domain.kaali_teeri.auction import auction, speaking_order
from app.domain.kaali_teeri.game import kt_game_from_payload
from app.domain.kaali_teeri.scoring import game_over, standings, totals
from app.domain.kaali_teeri.stats import (
    game_player_rows,
    hand_rows,
    head_to_head,
    player_stats,
    stats_game,
    summary,
)
from app.domain.kaali_teeri.validate import validate
from tests.domain.golden import camel, load, names

KT_NOW = 1_796_995_800_000  # 2026-12-11T13:30Z: when the app evaluated the fixtures
INVALID = names("kt-invalid-")
GAMES = [n for n in names("kt-") if n not in INVALID and n != "kt-set-stats"]


def test_the_fixture_set_is_present() -> None:
    assert len(GAMES) == 20
    assert len(INVALID) == 11


@pytest.mark.parametrize("name", GAMES + INVALID)
def test_validation_matches_the_app(name: str) -> None:
    fixture = load(name)
    expect = fixture["expect"]

    found = validate(kt_game_from_payload(fixture["game"]), KT_NOW)

    if expect["valid"]:
        assert found is None
    else:
        assert found is not None
        assert (found.reason, found.hand_index) == (expect["reason"], expect["handIndex"])


@pytest.mark.parametrize("name", GAMES)
def test_totals_places_and_end_match_the_app(name: str) -> None:
    fixture = load(name)
    expect = fixture["expect"]
    game = kt_game_from_payload(fixture["game"])

    places = {s.seat.id: s.rank for s in standings(game)} if game.status == "finished" else None

    assert totals(game) == expect["totals"]
    assert places == expect["places"]
    assert game_over(game) is expect["gameOver"]


def _auction_json(state: Any) -> dict[str, Any] | None:
    return None if state is None else camel(dataclasses.asdict(state))


@pytest.mark.parametrize("name", GAMES)
def test_auctions_match_the_app(name: str) -> None:
    fixture = load(name)
    game = kt_game_from_payload(fixture["game"])

    states = [
        _auction_json(auction(h.calls, speaking_order(game, i), game.rules))
        if h.mode == "auction"
        else None
        for i, h in enumerate(game.hands)
    ]

    assert states == fixture["expect"]["auctions"]


@pytest.mark.parametrize("name", GAMES)
def test_hand_rows_match_the_app(name: str) -> None:
    fixture = load(name)
    game = kt_game_from_payload(fixture["game"])

    rows = [camel(dataclasses.asdict(r)) for r in hand_rows(game)]

    expected = [{**h, "partnerIds": h["partnerIds"]} for h in fixture["expect"]["hands"]]
    assert [{**r, "partnerIds": list(r["partnerIds"])} for r in rows] == expected


@pytest.mark.parametrize("name", GAMES)
def test_game_players_and_summary_match_the_app(name: str) -> None:
    fixture = load(name)
    game = kt_game_from_payload(fixture["game"])

    assert camel(game_player_rows(game)) == fixture["expect"]["gamePlayers"]
    assert summary(game) == fixture["expect"]["summary"]


def test_player_stats_and_head_to_head_match_the_app() -> None:
    fixture = load("kt-set-stats")
    games = [stats_game(kt_game_from_payload(g)) for g in fixture["games"]]
    expect = fixture["expect"]

    for key, stats in expect["players"].items():
        assert camel(dataclasses.asdict(player_stats(games, key))) == stats, key
    for pair in expect["headToHead"]:
        found = camel(dataclasses.asdict(head_to_head(games, pair["a"], pair["b"])))
        assert {"a": pair["a"], "b": pair["b"], **found} == pair
