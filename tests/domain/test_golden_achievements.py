"""Parity with the app: achievements per game fixture and across the set fixture."""

from zoneinfo import ZoneInfo

import pytest

from app.domain.achievements import all_achievements, career_achievements
from app.domain.derive import build_facts
from app.domain.game import game_from_payload
from tests.domain.golden import GAME_NOW, camel, load, names
from tests.domain.test_golden_boards import set_facts

TZ = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize("name", names("game-"))
def test_game_achievements_match_the_app(name: str) -> None:
    fixture = load(name)
    facts = build_facts([game_from_payload(fixture["game"])], GAME_NOW)

    assert camel(all_achievements(facts, TZ)) == fixture["expect"]["achievements"]


def test_career_achievements_of_the_set_match_the_app() -> None:
    expected = load("set-month")["expect"]["career"]

    assert camel(career_achievements(set_facts())) == expected


def test_every_achievement_of_the_set_matches_the_app() -> None:
    expected = load("set-month")["expect"]["achievements"]

    assert camel(all_achievements(set_facts(), TZ)) == expected
