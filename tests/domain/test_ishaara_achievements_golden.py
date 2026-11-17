"""Parity with the app: Ishaara's game and career achievements, ladders, daily awards and seasons."""

from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.ishaara.achievements import (
    CAREER_CODES,
    GAME_CODES,
    MOMENT_CODES,
    IshCareerFacts,
    IshCareerRow,
    IshGameFacts,
    career_achievements,
    game_achievements,
    game_facts_of,
    ladder_counts,
)
from app.domain.ishaara.awards import (
    DAILY_CODES,
    SEASON_CODES,
    SEASON_MIN_GAMES,
    IshDayFacts,
    IshDayGame,
    IshMonthFacts,
    IshPlayRow,
    daily_awards,
    season_awards,
)
from app.domain.progression import Award, StandingRow
from tests.domain.golden import camel, load

GAMES = load("ishaara-achievements-games")
CAREER = load("ishaara-achievements-career")
DAILY = load("ishaara-awards-daily")
SEASON = load("ishaara-awards-season")


def _award_json(award: Award) -> dict[str, Any]:
    return {k: v for k, v in camel(award).items() if v is not None}


def _facts(raw: dict[str, Any]) -> IshGameFacts:
    """The fixture's facts, through the same reader the repository uses on a stored state."""
    state = {
        "id": raw["gameId"],
        "status": "finished",
        "players": raw["players"],
        "starting": raw["starting"],
        "log": raw["log"],
        "winner": raw["winner"],
        "endedBy": raw["endedBy"],
        "finishedAt": raw["finishedAt"],
        "updatedAt": raw["finishedAt"],
        "rules": {"bestOf": raw["bestOf"]},
        "match": raw["match"],
    }
    facts = game_facts_of(state)
    assert facts is not None
    return facts


def _career_row(raw: dict[str, Any]) -> IshCareerRow:
    return IshCareerRow(
        game_id=raw["gameId"],
        player_id=raw["playerId"],
        finished_at=raw["finishedAt"],
        won=raw["won"],
        role=raw["role"],
        words_found=raw["wordsFound"],
        clues_given=raw["cluesGiven"],
    )


def _play_row(raw: dict[str, Any]) -> IshPlayRow:
    return IshPlayRow(
        game_id=raw["gameId"],
        player_id=raw["playerId"],
        team=raw["team"],
        role=raw["role"],
        won=raw["won"],
        words_found=raw["wordsFound"],
        clues_given=raw["cluesGiven"],
        trap_hit=raw["trapHit"],
        finished_at=raw["finishedAt"],
    )


def _standing(raw: dict[str, Any]) -> StandingRow:
    return StandingRow(
        rank=raw["rank"],
        qualified=raw["qualified"],
        player_id=raw["playerId"],
        value=raw["value"],
        games=raw["games"],
        wins=raw["wins"],
    )


def _career_facts() -> IshCareerFacts:
    rows = tuple(_career_row(r) for r in CAREER["rows"])
    return IshCareerFacts(player_ids=tuple(sorted({r.player_id for r in rows})), rows=rows)


@pytest.mark.parametrize("case", GAMES["cases"], ids=[c["name"] for c in GAMES["cases"]])
def test_game_achievements_match_the_app(case: dict[str, Any]) -> None:
    earned = game_achievements(_facts(case["facts"]), ZoneInfo(GAMES["tz"]))

    assert camel(earned) == case["expect"]
    assert {e.code for e in earned} <= MOMENT_CODES | GAME_CODES


def test_the_game_cases_earn_every_moment_and_game_code() -> None:
    earned = {e["code"] for c in GAMES["cases"] for e in c["expect"]}
    assert earned == MOMENT_CODES | GAME_CODES


def test_career_achievements_match_the_app() -> None:
    earned = career_achievements(_career_facts())

    assert camel(earned) == CAREER["expect"]["earned"]
    assert {e.code for e in earned} <= CAREER_CODES


def test_ladder_counts_match_the_app() -> None:
    facts = _career_facts()
    for player_id, counts in CAREER["expect"]["ladders"].items():
        assert ladder_counts(facts, player_id) == counts, player_id


def test_careers_return_only_the_asked_players_rows() -> None:
    every = _career_facts()
    asked = every.player_ids[:2]

    earned = career_achievements(IshCareerFacts(player_ids=asked, rows=every.rows))

    expected = [e for e in CAREER["expect"]["earned"] if e["playerId"] in asked]
    assert camel(earned) == expected and earned


def test_a_player_without_rows_earns_nothing_and_counts_zero() -> None:
    facts = IshCareerFacts(player_ids=("nobody",), rows=_career_facts().rows)

    assert career_achievements(facts) == []
    assert ladder_counts(facts, "nobody") == {
        "wins": 0,
        "games": 0,
        "giver_wins": 0,
        "clues": 0,
        "found": 0,
        "crew": 0,
    }


def test_an_unfinished_abandoned_or_winnerless_state_has_no_facts() -> None:
    state = {**GAMES["cases"][0]["facts"], "status": "finished"}
    assert game_facts_of({**state, "status": "active"}) is None
    assert game_facts_of({**state, "endedBy": "abandoned"}) is None
    assert game_facts_of({**state, "winner": None}) is None


@pytest.mark.parametrize("day", DAILY["days"], ids=[d["date"] for d in DAILY["days"]])
def test_daily_awards_match_the_app(day: dict[str, Any]) -> None:
    games = tuple(
        IshDayGame(
            game_id=g["gameId"],
            ended_by=g["endedBy"],
            started_at=g["startedAt"],
            finished_at=g["finishedAt"],
        )
        for g in day["games"]
    )
    rows = tuple(_play_row(r) for r in day["rows"])

    awards = daily_awards(IshDayFacts(start_ms=0, end_ms=0, rows=rows, games=games))

    assert [_award_json(a) for a in awards] == day["expect"]
    assert {a.code for a in awards} <= DAILY_CODES


@pytest.mark.parametrize("season", SEASON["seasons"], ids=[s["season"] for s in SEASON["seasons"]])
def test_season_awards_match_the_app(season: dict[str, Any]) -> None:
    standings = [_standing(r) for r in season["standings"]]
    last = [_standing(r) for r in season["last"]]
    rows = tuple(_play_row(r) for r in season["rows"])

    awards = season_awards(standings, last, IshMonthFacts(start_ms=0, end_ms=0, rows=rows))

    assert [_award_json(a) for a in awards] == season["expect"]
    assert {a.code for a in awards} <= SEASON_CODES
    assert SEASON["minGames"] == SEASON_MIN_GAMES


def test_the_award_fixtures_give_every_code() -> None:
    daily = {a["code"] for d in DAILY["days"] for a in d["expect"]}
    season = {a["code"] for s in SEASON["seasons"] for a in s["expect"]}
    assert daily == DAILY_CODES
    assert season == SEASON_CODES
