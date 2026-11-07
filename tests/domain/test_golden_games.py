"""Parity with the app: every game fixture's validation, totals, places and derived rows."""

import pytest

from app.domain.derive import game_ended_at, game_facts, round_facts
from app.domain.game import game_from_payload
from app.domain.rules import standings, totals
from app.domain.validate import validate
from tests.domain.golden import GAME_NOW, camel, load, names

GAMES = names("game-")
INVALID = names("invalid-")


def test_the_fixture_set_is_present() -> None:
    assert len(GAMES) == 9
    assert len(INVALID) == 8


@pytest.mark.parametrize("name", GAMES + INVALID)
def test_validation_matches_the_app(name: str) -> None:
    fixture = load(name)
    expect = fixture["expect"]

    result = validate(game_from_payload(fixture["game"]), GAME_NOW)

    assert result.ok is expect["valid"]
    if not expect["valid"]:
        assert result.reason == expect["reason"]
        assert result.round_index == expect["roundIndex"]


@pytest.mark.parametrize("name", GAMES)
def test_end_time_totals_and_places_match_the_app(name: str) -> None:
    fixture = load(name)
    expect = fixture["expect"]
    game = game_from_payload(fixture["game"])

    ended = game_ended_at(game, GAME_NOW)

    assert ended.at == expect["endedAt"]
    assert totals(game) == expect["totals"]
    places = None if ended.at is None else {s.seat.id: s.rank for s in standings(game)}
    assert places == expect["places"]


@pytest.mark.parametrize("name", GAMES)
def test_round_scores_match_the_app(name: str) -> None:
    fixture = load(name)
    game = game_from_payload(fixture["game"])

    assert camel(round_facts(game)) == fixture["expect"]["roundScores"]


@pytest.mark.parametrize("name", GAMES)
def test_game_players_match_the_app(name: str) -> None:
    fixture = load(name)
    game = game_from_payload(fixture["game"])
    ended = game_ended_at(game, GAME_NOW)

    assert camel(game_facts(game, ended.at)) == fixture["expect"]["gamePlayers"]
