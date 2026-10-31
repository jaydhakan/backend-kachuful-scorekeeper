"""Parity with the app: daily awards and seasons of the set fixture."""

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.awards import SEASON_MIN_ROUNDS, daily_awards, season_awards
from app.domain.leaderboard import (
    Metric,
    leaderboard,
    previous_window,
    tallies_from_facts,
)
from app.domain.time import day_range_ms, month_range_ms, previous_month
from tests.domain.golden import camel, load
from tests.domain.test_golden_boards import set_facts

TZ = ZoneInfo("Asia/Kolkata")
EXPECT = load("set-month")["expect"]


def _award_json(award: Any) -> dict[str, Any]:
    out = camel(award)
    return {k: v for k, v in out.items() if v is not None}


@pytest.mark.parametrize("case", EXPECT["daily"], ids=[d["date"] for d in EXPECT["daily"]])
def test_daily_awards_match_the_app(case: dict[str, Any]) -> None:
    facts = set_facts()
    start, end = day_range_ms(date.fromisoformat(case["date"]), TZ)
    rounds = [r for r in facts.rounds if start <= r.done_at < end]

    awards = daily_awards(
        rounds, tallies_from_facts(facts.rounds, facts.players, start, end).values()
    )

    assert [_award_json(a) for a in awards] == case["awards"]


def _month_board(month: str) -> Any:
    facts = set_facts()
    start, end = month_range_ms(month, TZ)
    prev_start, prev_end = previous_window(start, end)
    return leaderboard(
        tallies_from_facts(facts.rounds, facts.players, start, end).values(),
        tallies_from_facts(facts.rounds, facts.players, prev_start, prev_end).values(),
        Metric.AVG_PER_ROUND,
        SEASON_MIN_ROUNDS,
    )


@pytest.mark.parametrize("case", EXPECT["season"], ids=[s["season"] for s in EXPECT["season"]])
def test_seasons_match_the_app(case: dict[str, Any]) -> None:
    standings = _month_board(case["season"])

    awards = season_awards(standings, _month_board(previous_month(case["season"])))

    assert camel(standings.rows) == case["standings"]
    assert [_award_json(a) for a in awards] == case["awards"]
