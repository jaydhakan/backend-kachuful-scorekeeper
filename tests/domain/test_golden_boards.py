"""Parity with the app's set fixture: leaderboards for every metric and range."""

from typing import Any

import pytest

from app.domain.derive import Facts, build_facts
from app.domain.game import game_from_payload
from app.domain.leaderboard import (
    Metric,
    leaderboard,
    min_rounds_for,
    previous_window,
    tallies_from_facts,
)
from tests.domain.golden import camel, load

SET = load("set-month")


def set_facts() -> Facts:
    """Facts for the set fixture's games, as the app built them."""
    return build_facts([game_from_payload(g) for g in SET["games"]], SET["now"])


QUERIES = SET["expect"]["leaderboard"]


@pytest.mark.parametrize(
    "case", QUERIES, ids=[f"{q['query']['range']}-{q['query']['metric']}" for q in QUERIES]
)
def test_leaderboard_matches_the_app(case: dict[str, Any]) -> None:
    facts = set_facts()
    query = case["query"]
    start, end = query["from"], query["to"]
    prev_start, prev_end = previous_window(start, end)
    previous = (
        tallies_from_facts(facts.rounds, facts.players, prev_start, prev_end).values()
        if prev_end > prev_start
        else None
    )

    board = leaderboard(
        tallies_from_facts(facts.rounds, facts.players, start, end).values(),
        previous,
        Metric(query["metric"]),
        min_rounds_for(start, end),
    )

    assert board.min_rounds == case["minRounds"]
    assert camel(board.rows) == case["rows"]
