"""Ishaara rematch: same seats, fresh words, the other team starts, clue-givers rotate."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.ishaara_rematch import next_match
from tests.factories import make_user
from tests.ishaara_builder import (
    ClientFor,
    Table,
    act,
    client_for_fixture,  # noqa: F401 - fixture
    create,
    make_table,
    read,
    start,
)

FRESH = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india",
         "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo",
         "sierra", "tango", "uniform", "victor", "whiskey", "xray", "yankee"]  # fmt: skip


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


async def _finished(table: Table, **changes: Any) -> dict[str, Any]:
    state, _ = await start(table, **changes)
    response = await act(table, "host", state, "resign", team="moon")
    assert response.status_code == 200, response.text
    game: dict[str, Any] = response.json()["game"]
    return game


async def _link(session: AsyncSession, table: Table, k: int) -> User:
    """Give guest k an account, so rotation can pick them."""
    user = await make_user(session, email=f"guest{k}@example.com", player_name=None)
    user.player_id = table.guests[k].id
    await session.flush()
    return user


async def test_a_rematch_rotates_clue_givers_and_switches_the_starting_team(
    table: Table, session: AsyncSession
) -> None:
    await _link(session, table, 0)
    old = await _finished(table)
    path = f"/api/ishaara/games/{old['id']}/rematch"
    new_id = str(uuid.uuid4())

    created = await table.clients["host"].post(path, json={"id": new_id, "words": FRESH})
    repeated = await table.clients["host"].post(
        path, json={"id": str(uuid.uuid4()), "words": FRESH}
    )

    assert created.status_code == 201, created.text
    game = created.json()["game"]
    assert game["id"] == new_id and game["words"] == FRESH and game["status"] == "waiting"
    assert game["starting"] != old["starting"]
    givers = {p["team"]: p["playerId"] for p in game["players"] if p["role"] == "clue-giver"}
    # Sun rotates to the linked guest; Moon's guest has no account, so Moon keeps its clue-giver.
    assert givers == {"sun": str(table.guests[0].id), "moon": str(table.moon.player_id)}
    assert game["match"] == {"game": 1, "sunWins": 0, "moonWins": 0}
    assert (repeated.status_code, repeated.json()["game"]["id"]) == (200, new_id)
    followed = await read(table, old["id"])
    assert followed["next"] == new_id and followed["rev"] == old["rev"] + 1


async def test_the_body_picks_clue_givers(table: Table, session: AsyncSession) -> None:
    await _link(session, table, 1)
    old = await _finished(table)

    response = await table.clients["host"].post(
        f"/api/ishaara/games/{old['id']}/rematch",
        json={
            "id": str(uuid.uuid4()),
            "words": FRESH,
            "clueGivers": {"moon": str(table.guests[1].id)},
        },
    )

    givers = {
        p["team"]: p["playerId"]
        for p in response.json()["game"]["players"]
        if p["role"] == "clue-giver"
    }
    assert givers["moon"] == str(table.guests[1].id)


async def test_a_picked_clue_giver_must_sit_on_that_team_and_have_an_account(table: Table) -> None:
    old = await _finished(table)
    path = f"/api/ishaara/games/{old['id']}/rematch"

    wrong_team = await table.clients["host"].post(
        path,
        json={
            "id": str(uuid.uuid4()),
            "words": FRESH,
            "clueGivers": {"sun": str(table.guests[1].id)},
        },
    )
    no_account = await table.clients["host"].post(
        path,
        json={
            "id": str(uuid.uuid4()),
            "words": FRESH,
            "clueGivers": {"sun": str(table.guests[0].id)},
        },
    )

    assert (wrong_team.status_code, wrong_team.json()["reason"]) == (422, "clue-givers")
    assert (no_account.status_code, no_account.json()["reason"]) == (422, "clue-giver-account")


async def test_a_best_of_three_carries_the_score(table: Table) -> None:
    rules = {"timerSec": None, "clueEntry": "typed", "packs": ["test"], "bestOf": 3}
    old = await _finished(table, rules=rules)

    response = await table.clients["host"].post(
        f"/api/ishaara/games/{old['id']}/rematch", json={"id": str(uuid.uuid4()), "words": FRESH}
    )

    assert response.json()["game"]["match"] == {"game": 2, "sunWins": 1, "moonWins": 0}


def test_a_match_ends_at_two_wins() -> None:
    state = {"rules": {"bestOf": 3}, "match": {"game": 3, "sunWins": 2, "moonWins": 1}}

    assert next_match(state) == {"game": 1, "sunWins": 0, "moonWins": 0}


async def test_only_a_finished_game_by_its_host_gets_a_rematch(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    waiting = (await create(table))["game"]
    finished = await _finished(table)
    stranger = await make_user(session, email="who@example.com", player_name="Who")
    body = {"id": str(uuid.uuid4()), "words": FRESH}

    early = await table.clients["host"].post(
        f"/api/ishaara/games/{waiting['id']}/rematch", json=body
    )
    by_sun = await table.clients["sun"].post(
        f"/api/ishaara/games/{finished['id']}/rematch", json=body
    )
    by_stranger = await (await client_for(stranger)).post(
        f"/api/ishaara/games/{finished['id']}/rematch", json=body
    )
    bad_words = await table.clients["host"].post(
        f"/api/ishaara/games/{finished['id']}/rematch", json={**body, "words": FRESH[:24]}
    )
    taken = await table.clients["host"].post(
        f"/api/ishaara/games/{finished['id']}/rematch", json={**body, "id": waiting["id"]}
    )

    assert (early.status_code, early.json()["code"], early.json()["reason"]) == (
        422,
        "invalid_action",
        "not-finished",
    )
    assert (by_sun.status_code, by_sun.json()["code"]) == (403, "forbidden")
    assert by_stranger.status_code == 404
    assert (bad_words.status_code, bad_words.json()["reason"]) == (422, "words")
    assert (taken.status_code, taken.json()["code"]) == (409, "id_conflict")
