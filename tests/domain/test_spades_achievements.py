"""Parity with the app: Spades achievements, daily awards and seasons over stored games of both kinds."""

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.progression import Earned, WinTally, win_rate_standings
from app.domain.spades.achievements import (
    CAREER_CODES,
    GAME_CODES,
    LADDER_IDS,
    MOMENT_CODES,
    SpCareerFacts,
    SpRow,
    career_achievements,
    game_achievements,
    game_facts_of,
    ladder_counts,
)
from app.domain.spades.awards import (
    DAILY_CODES,
    DAY_MOMENT_CODES,
    SEASON_CODES,
    SEASON_MIN_GAMES,
    SpDayFacts,
    SpMonthFacts,
    daily_awards,
    season_awards,
)
from app.domain.spades.stats import player_rows
from app.domain.time import day_range_ms, month_range_ms, previous_month
from tests.domain.golden import camel, load, names

ACHIEVEMENTS = names("spades-achievements-")
AWARDS = names("spades-awards-")
TZ = ZoneInfo("Asia/Kolkata")


def test_the_fixture_sets_are_present() -> None:
    assert len(ACHIEVEMENTS) == 4
    assert len(AWARDS) == 2


def test_the_codes_are_the_plans_59() -> None:
    sizes = (len(MOMENT_CODES), len(GAME_CODES), len(CAREER_CODES))
    assert sizes == (13, 11, 20)
    assert (len(DAILY_CODES), len(SEASON_CODES)) == (7, 8)
    assert LADDER_IDS == ("wins", "games", "hands", "made", "nils", "crew")


def _id(stored: dict[str, Any]) -> str:
    payload = stored["payload"]
    return str(payload["id"] if stored["kind"] == "spades-online" else payload["syncId"])


def _game(stored: dict[str, Any]) -> Any:
    return game_facts_of(stored["kind"], _id(stored), stored["payload"])


def _rows(stored: dict[str, Any]) -> list[SpRow]:
    """The `spades_game_players` rows the server writes when the game finishes."""
    facts = _game(stored)
    if facts.status != "finished" or not facts.result:
        return []
    ended = facts.finished_at if facts.finished_at is not None else facts.updated_at
    return [
        SpRow(
            game_id=facts.game_id,
            player_id=r.player_id,
            ended_at=ended,
            result=r.result,
            partner_id=r.partner_id,
            hands=r.tally.hands,
            bids=r.tally.bids,
            bids_made=r.tally.bids_made,
            nils_made=r.tally.nils_made + r.tally.blind_nils_made,
            bags=r.tally.bags,
        )
        for r in player_rows(list(facts.hands), list(facts.seats), facts.result)
    ]


def _all_rows(games: list[dict[str, Any]]) -> list[SpRow]:
    return [r for g in games for r in _rows(g)]


def _career(games: list[dict[str, Any]]) -> SpCareerFacts:
    rows = _all_rows(games)
    tables: dict[str, tuple[str, ...]] = {}
    for r in rows:
        if r.result is not None:
            tables[r.game_id] = (*tables.get(r.game_id, ()), r.player_id)
    players = tuple(sorted({r.player_id for r in rows}))
    return SpCareerFacts(player_ids=players, rows=tuple(rows), tables=tables)


def _key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (e["playerId"], e["code"], e["gameId"] or "", e["roundIndex"] or -1, e["earnedAt"])


def _earned(xs: list[Earned]) -> list[dict[str, Any]]:
    return sorted(camel(xs), key=_key)


@pytest.mark.parametrize("name", ACHIEVEMENTS)
def test_each_games_achievements_match_the_app(name: str) -> None:
    f = load(name)
    for stored, want in zip(f["games"], f["expect"]["games"], strict=True):
        assert _id(stored) == want["gameId"]
        assert _earned(game_achievements(_game(stored), TZ)) == sorted(want["earned"], key=_key)


@pytest.mark.parametrize("name", ACHIEVEMENTS)
def test_careers_and_ladders_match_the_app(name: str) -> None:
    f = load(name)
    facts = _career(f["games"])

    assert _earned(career_achievements(facts)) == sorted(f["expect"]["career"], key=_key)
    for player, counts in f["expect"]["ladders"].items():
        assert ladder_counts(facts, player) == counts


def _ended_in(games: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    out = []
    for g in games:
        facts = _game(g)
        ended = facts.finished_at if facts.finished_at is not None else facts.updated_at
        if facts.status == "finished" and facts.result and start <= ended < end:
            out.append(g)
    return out


def _award_json(award: Any) -> dict[str, Any]:
    return {k: v for k, v in camel(award).items() if v is not None}


def _day(games: list[dict[str, Any]], day: str) -> SpDayFacts:
    start, end = day_range_ms(date.fromisoformat(day), TZ)
    ended = _ended_in(games, start, end)
    moments = tuple(
        (e.player_id, e.code)
        for g in ended
        for e in game_achievements(_game(g), TZ)
        if e.code in DAY_MOMENT_CODES
    )
    return SpDayFacts(start, end, tuple(_all_rows(ended)), moments)


def _standings(games: list[dict[str, Any]], month: str) -> list[Any]:
    start, end = month_range_ms(month, TZ)
    tallies: dict[str, WinTally] = {}
    for r in _all_rows(_ended_in(games, start, end)):
        if r.result is None:
            continue
        t = tallies.get(r.player_id, WinTally(r.player_id, 0, 0))
        tallies[r.player_id] = WinTally(r.player_id, t.games + 1, t.wins + (r.result == "win"))
    return win_rate_standings(tallies.values(), SEASON_MIN_GAMES)


def _by_player(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: str(r["playerId"]))


@pytest.mark.parametrize("name", AWARDS)
def test_daily_awards_match_the_app(name: str) -> None:
    f = load(name)
    for case in f["expect"]["daily"]:
        awards = daily_awards(_day(f["games"], case["date"]))
        assert [_award_json(a) for a in awards] == case["awards"], case["date"]


@pytest.mark.parametrize("name", AWARDS)
def test_seasons_match_the_app(name: str) -> None:
    f = load(name)
    for case in f["expect"]["season"]:
        month = case["season"]
        standings = _standings(f["games"], month)
        last = _standings(f["games"], previous_month(month))
        start, end = month_range_ms(month, TZ)
        rows = tuple(_all_rows(_ended_in(f["games"], start, end)))

        awards = season_awards(standings, last, SpMonthFacts(start, end, rows))

        assert _by_player(camel(standings)) == _by_player(case["standings"]), month
        assert [_award_json(a) for a in awards] == case["awards"], month
