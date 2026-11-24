"""Ishaara's achievements and awards through the real action path: a finished game's rows, careers,
recompute, delete, abandon, daily awards and ladders, read from what the actions stored."""

import argparse
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Game
from app.domain.enums import GameKind, ProgressionGame
from app.domain.progression import Award
from app.domain.time import day_range_ms, to_ms
from app.jobs import recompute_achievements
from app.repositories.ishaara_progression import ishaara_progression_repository as repo
from app.services.ishaara_games import IshaaraGamesService
from app.services.progression.hooks import progress_game
from app.services.progression.registry import ISHAARA
from app.settings import settings
from tests.ishaara_builder import (
    TEAM_WORDS,
    ClientFor,
    Clock,
    Table,
    act,
    cards_of,
    client_for_fixture,  # noqa: F401 - fixture
    freeze,
    make_table,
    start,
)

# Midday in APP_TZ, so no night_owl.
DAY = date(2026, 11, 16)
NOON_MS = to_ms(datetime(2026, 11, 16, 12, 0, tzinfo=settings.tz))
STEP_SECONDS = 1.0

Row = tuple[str, str, int | None, int]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = freeze(monkeypatch)
    clock.ms = NOON_MS
    return clock


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


async def _do(
    table: Table, clock: Clock, who: str, state: dict[str, Any], type_: str, **fields: Any
) -> dict[str, Any]:
    clock.advance(STEP_SECONDS)
    response = await act(table, who, state, type_, **fields)
    assert response.status_code == 200, response.text
    game: dict[str, Any] = response.json()["game"]
    return game


async def _clue(table: Table, clock: Clock, state: dict[str, Any], number: int | str) -> Any:
    team = state["turn"]
    return await _do(table, clock, team, state, "clue", word=TEAM_WORDS[team], number=number)


async def _guesses(
    table: Table, clock: Clock, state: dict[str, Any], cards: list[int]
) -> dict[str, Any]:
    for card in cards:
        state = await _do(table, clock, "host", state, "guess", card=card)
    return state


async def _meld_overtime_and_win(table: Table, clock: Clock) -> tuple[dict[str, Any], int]:
    """The starting team finds a 3 and one more, the others hit one of its cards, then it
    finds the rest on an ∞ clue: (final state, first clue's ms)."""
    state, key = await start(table)
    s = state["starting"]
    state = await _clue(table, clock, state, 3)
    first_clue = clock.ms
    state = await _guesses(table, clock, state, cards_of(key, s, state)[:4])
    state = await _clue(table, clock, state, 1)
    state = await _guesses(table, clock, state, cards_of(key, s, state)[:1])
    state = await _clue(table, clock, state, "inf")
    state = await _guesses(table, clock, state, cards_of(key, s, state))
    assert (state["status"], state["winner"], state["endedBy"]) == ("finished", s, "all-found")
    return state, first_clue


def _team(table: Table, team: str) -> set[str]:
    giver = table.sun if team == "sun" else table.moon
    guest = table.guests[0 if team == "sun" else 1]
    return {str(giver.player_id), str(guest.id)}


async def _rows(session: AsyncSession, game_id: str) -> list[Achievement]:
    stmt = (
        select(Achievement)
        .where(
            Achievement.game == ProgressionGame.ISHAARA, Achievement.game_id == uuid.UUID(game_id)
        )
        .order_by(Achievement.code, Achievement.player_id)
    )
    return list((await session.execute(stmt)).scalars())


def _shape(rows: list[Achievement]) -> set[Row]:
    return {(r.code, str(r.player_id), r.round_index, to_ms(r.earned_at)) for r in rows}


async def test_a_finished_game_stores_its_moments_game_codes_and_first_wins(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, _ = await _meld_overtime_and_win(table, clock)
    winners = _team(table, state["starting"])
    moments = [e for e in state["log"] if e["type"] == "guess"]
    finished = state["finishedAt"]

    rows = await _rows(session, state["id"])

    expected: set[Row] = set()
    for pid in winners:
        expected |= {
            ("mind_meld", pid, 0, moments[2]["at"]),
            ("overtime", pid, 0, moments[3]["at"]),
            ("winner", pid, None, finished),
            ("flawless", pid, None, finished),
            ("speedrun", pid, None, finished),
            ("first_win", pid, None, finished),
        }
    assert _shape(rows) == expected
    assert all(r.period is None for r in rows)
    assert all(r.data == {} for r in rows)


async def test_recomputing_keeps_the_same_rows_and_times(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, _ = await _meld_overtime_and_win(table, clock)
    before = {(r.id, r.code, r.player_id, r.earned_at) for r in await _rows(session, state["id"])}
    players = {uuid.UUID(p["playerId"]) for p in state["players"]}

    added = await progress_game(
        session, GameKind.ISHAARA, uuid.UUID(state["id"]), players, finished=True
    )
    await recompute_achievements.run(argparse.Namespace(game="ishaara"))

    session.expire_all()
    after = {(r.id, r.code, r.player_id, r.earned_at) for r in await _rows(session, state["id"])}
    assert added.earned == [] and after == before


async def test_deleting_the_game_removes_its_rows_and_careers(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, _ = await _meld_overtime_and_win(table, clock)

    await IshaaraGamesService(session).delete(uuid.UUID(state["id"]), table.host)

    assert await _rows(session, state["id"]) == []
    careers = select(Achievement).where(Achievement.game == ProgressionGame.ISHAARA)
    assert list((await session.execute(careers)).scalars()) == []


async def test_an_abandoned_game_stores_nothing(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, key = await start(table)
    state = await _clue(table, clock, state, 3)
    state = await _guesses(table, clock, state, cards_of(key, state["starting"], state)[:3])
    game_id = uuid.UUID(state["id"])
    idle = clock.now() - timedelta(hours=3)
    await session.execute(update(Game).where(Game.id == game_id).values(updated_at=idle))

    assert await IshaaraGamesService(session).end_idle(game_id)

    assert await _rows(session, state["id"]) == []


async def test_the_days_awards_read_the_stored_rows_and_first_clue(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, first_clue = await _meld_overtime_and_win(table, clock)
    winners = sorted(_team(table, state["starting"]))
    everyone = sorted(p["playerId"] for p in state["players"])

    awards = await ISHAARA.daily_awards(session, DAY)

    took = state["finishedAt"] - first_clue
    assert awards == [
        *(Award("day_speed", pid, took) for pid in winners),
        *(Award("day_grinder", pid, 1) for pid in everyone),
    ]
    assert await ISHAARA.daily_awards(session, DAY + timedelta(days=1)) == []


async def test_ladders_count_the_stored_games(
    session: AsyncSession, table: Table, clock: Clock
) -> None:
    state, _ = await _meld_overtime_and_win(table, clock)
    giver = table.sun if state["starting"] == "sun" else table.moon
    params = {"game": "ishaara", "playerId": str(giver.player_id)}

    response = await table.clients["host"].get("/api/achievements/ladders", params=params)

    assert response.status_code == 200, response.text
    # 8 found: the other team's guess turned the ninth card.
    assert response.json()["counts"] == {
        "wins": 1,
        "games": 1,
        "giver_wins": 1,
        "clues": 2,
        "found": 8,
        "crew": 3,
    }


async def test_the_facts_loaders_read_a_fixed_number_of_queries(
    session: AsyncSession, table: Table, clock: Clock, queries: list[str]
) -> None:
    state, _ = await _meld_overtime_and_win(table, clock)
    players = {uuid.UUID(p["playerId"]) for p in state["players"]}
    start_ms, end_ms = day_range_ms(DAY, settings.tz)

    queries.clear()
    game = await repo.game_facts(session, uuid.UUID(state["id"]))
    game_queries = list(queries)
    queries.clear()
    career = await repo.career_facts(session, players)
    day = await repo.day_facts(session, start_ms, end_ms)
    month = await repo.month_facts(session, start_ms, end_ms)

    assert game is not None and len(game.log) == len(state["log"])
    assert len(game_queries) == 1
    assert len(career.rows) == 4 and len(day.rows) == 4 and len(month.rows) == 4
    assert [g.ended_by for g in day.games] == ["all-found"]
    # One for careers, two for the day (rows, then its games), one for the month.
    assert len(queries) == 4 and all("payload" not in q for q in queries)
