"""Parity with the app: Kaali Teeri's achievements, ladders, daily awards and seasons."""

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.kaali_teeri.achievements import (
    CAREER_CODES,
    GAME_CODES,
    MOMENT_CODES,
    KtCareerFacts,
    KtGameFacts,
    KtHandFact,
    KtSeatFact,
    career_achievements,
    deck_of,
    game_achievements,
    ladder_counts,
)
from app.domain.kaali_teeri.awards import (
    DAILY_CODES,
    SEASON_CODES,
    SEASON_MIN_GAMES,
    KtDayFacts,
    KtMonthFacts,
    daily_awards,
    season_awards,
)
from app.domain.kaali_teeri.game import KtGame, kt_game_from_payload
from app.domain.kaali_teeri.stats import game_player_rows, hand_rows
from app.domain.progression import Award, Earned, StandingRow, WinTally, win_rate_standings
from app.domain.time import day_range_ms, month_range_ms, previous_month
from tests.domain.golden import load, names

ACHIEVEMENTS = names("kaali-teeri-achievements-")
AWARDS = names("kaali-teeri-awards-")


def _ended_at(game: KtGame) -> int | None:
    """As KtGamesService stores it: finishedAt, else the last change."""
    return (game.finished_at or game.updated_at) if game.status == "finished" else None


def _game_facts(game: KtGame) -> KtGameFacts:
    """One game's facts from its payload, as an upload writes its kt_hands and kt_game_players."""
    deck = deck_of(len(game.players))
    hands = tuple(
        KtHandFact(
            game_id=game.id,
            hand_index=r.hand_index,
            done_at=game.updated_at if r.done_at is None else r.done_at,
            deck=deck,
            bidder_id=r.bidder_id,
            bid=r.bid,
            forced=r.forced,
            partner_ids=r.partner_ids,
            slots=r.slots,
            team_points=r.team_points,
            made=r.made,
            sweep=r.sweep,
            scores=r.scores,
        )
        for r in hand_rows(game)
    )
    ended = _ended_at(game)
    seats = tuple(
        KtSeatFact(
            game_id=game.id,
            player_id=p.player_id,
            points=p.points,
            place=p.place,
            ended_at=ended,
            hands_bid=p.hands_bid,
            bids_made=p.bids_made,
            partner_made=p.partner_made,
        )
        for p in game_player_rows(game)
    )
    return KtGameFacts(game_id=game.id, hands=hands, seats=seats)


def _games(fixture: dict[str, Any]) -> list[KtGameFacts]:
    out = []
    for raw in fixture["games"]:
        game = kt_game_from_payload(raw)
        out.append(_game_facts(game))
    return out


def _career(games: list[KtGameFacts]) -> KtCareerFacts:
    hands = tuple(h for g in games for h in g.hands)
    seats = tuple(s for g in games for s in g.seats)
    return KtCareerFacts(
        player_ids=tuple(sorted({s.player_id for s in seats})), hands=hands, seats=seats
    )


def _hand_json(h: KtHandFact) -> dict[str, Any]:
    return {
        "gameId": h.game_id,
        "handIndex": h.hand_index,
        "doneAt": h.done_at,
        "deck": h.deck,
        "bidderId": h.bidder_id,
        "bid": h.bid,
        "forced": h.forced,
        "partnerIds": list(h.partner_ids),
        "slots": h.slots,
        "teamPoints": h.team_points,
        "made": h.made,
        "sweep": str(h.sweep),
        "scores": h.scores,
    }


def _seat_json(s: KtSeatFact) -> dict[str, Any]:
    return {
        "gameId": s.game_id,
        "playerId": s.player_id,
        "points": s.points,
        "place": s.place,
        "endedAt": s.ended_at,
        "handsBid": s.hands_bid,
        "bidsMade": s.bids_made,
        "partnerMade": s.partner_made,
    }


def _earned_json(e: Earned) -> dict[str, Any]:
    return {
        "code": e.code,
        "playerId": e.player_id,
        "gameId": e.game_id,
        "roundIndex": e.round_index,
        "period": e.period,
        "earnedAt": e.earned_at,
        "data": e.data,
    }


def _award_json(a: Award) -> dict[str, Any]:
    out: dict[str, Any] = {"code": a.code, "playerId": a.player_id, "value": pytest.approx(a.value)}
    if a.game_id is not None:
        out |= {"gameId": a.game_id, "roundIndex": a.round_index}
    return out


def _by_key(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(e: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            e["playerId"],
            e["code"],
            e["gameId"] or "",
            "" if e["roundIndex"] is None else str(e["roundIndex"]),
            e["period"] or "",
        )

    return sorted(rows, key=key)


def test_the_fixture_set_is_present() -> None:
    assert len(ACHIEVEMENTS) == 3
    assert len(AWARDS) == 2


def test_the_catalogue_has_the_plans_counts() -> None:
    assert (len(MOMENT_CODES), len(GAME_CODES), len(CAREER_CODES)) == (10, 11, 23)
    assert (len(DAILY_CODES), len(SEASON_CODES)) == (7, 7)


@pytest.mark.parametrize("name", ACHIEVEMENTS + AWARDS)
def test_facts_from_the_payloads_match_the_app(name: str) -> None:
    fixture = load(name)
    games = _games(fixture)

    assert [_hand_json(h) for g in games for h in g.hands] == fixture["facts"]["hands"]
    assert [_seat_json(s) for g in games for s in g.seats] == fixture["facts"]["seats"]


@pytest.mark.parametrize("name", ACHIEVEMENTS)
def test_each_games_achievements_match_the_app(name: str) -> None:
    fixture = load(name)
    tz = ZoneInfo(fixture["tz"])

    for facts, expect in zip(_games(fixture), fixture["expect"]["games"], strict=True):
        assert facts.game_id == expect["gameId"]
        found = [_earned_json(e) for e in game_achievements(facts, tz)]
        assert {e["code"] for e in found} <= MOMENT_CODES | GAME_CODES
        assert _by_key(found) == _by_key(expect["earned"])


@pytest.mark.parametrize("name", ACHIEVEMENTS)
def test_careers_and_ladders_match_the_app(name: str) -> None:
    fixture = load(name)
    facts = _career(_games(fixture))

    found = [_earned_json(e) for e in career_achievements(facts)]

    assert {e["code"] for e in found} <= CAREER_CODES
    assert _by_key(found) == _by_key(fixture["expect"]["career"])
    for player_id, counts in fixture["expect"]["ladders"].items():
        assert ladder_counts(facts, player_id) == counts


def test_a_career_holds_only_the_players_asked_for() -> None:
    games = _games(load("kaali-teeri-achievements-career"))
    everyone = _career(games)
    ana = KtCareerFacts(player_ids=("ana",), hands=everyone.hands, seats=everyone.seats)

    found = career_achievements(ana)

    assert found
    assert {e.player_id for e in found} == {"ana"}
    assert sorted(found, key=lambda e: e.key) == sorted(
        (e for e in career_achievements(everyone) if e.player_id == "ana"), key=lambda e: e.key
    )


@pytest.mark.parametrize("name", AWARDS)
def test_daily_awards_match_the_app(name: str) -> None:
    fixture = load(name)
    tz = ZoneInfo(fixture["tz"])
    hands = tuple(h for g in _games(fixture) for h in g.hands)

    for day in fixture["expect"]["daily"]:
        start, end = day_range_ms(date.fromisoformat(day["date"]), tz)
        found = daily_awards(KtDayFacts(start_ms=start, end_ms=end, hands=hands))
        assert [_award_json(a) for a in found] == day["awards"]
        assert {a.code for a in found} <= DAILY_CODES


def _tallies(seats: tuple[KtSeatFact, ...], start: int, end: int) -> list[WinTally]:
    """As month_tallies counts them: ended seats whose game ended in [start, end)."""
    by: dict[str, tuple[int, int]] = {}
    for s in seats:
        if s.place is None or s.ended_at is None or not start <= s.ended_at < end:
            continue
        games, wins = by.get(s.player_id, (0, 0))
        by[s.player_id] = (games + 1, wins + (s.place == 1))
    return [WinTally(p, g, w) for p, (g, w) in by.items()]


def _standing_json(r: StandingRow) -> dict[str, Any]:
    return {
        "playerId": r.player_id,
        "rank": r.rank,
        "qualified": r.qualified,
        "value": pytest.approx(r.value),
        "games": r.games,
        "wins": r.wins,
    }


@pytest.mark.parametrize("name", AWARDS)
def test_seasons_match_the_app(name: str) -> None:
    fixture = load(name)
    tz = ZoneInfo(fixture["tz"])
    games = _games(fixture)
    hands = tuple(h for g in games for h in g.hands)
    seats = tuple(s for g in games for s in g.seats)

    for expect in fixture["expect"]["seasons"]:
        month = expect["season"]
        start, end = month_range_ms(month, tz)
        last_start, last_end = month_range_ms(previous_month(month), tz)
        standings = win_rate_standings(_tallies(seats, start, end), SEASON_MIN_GAMES)
        last = win_rate_standings(_tallies(seats, last_start, last_end), SEASON_MIN_GAMES)

        found = season_awards(
            standings, last, KtMonthFacts(start_ms=start, end_ms=end, hands=hands)
        )

        rows = [_standing_json(r) for r in standings]
        assert rows == expect["standings"]
        assert [_award_json(a) for a in found] == expect["awards"]
        assert {a.code for a in found} <= SEASON_CODES
