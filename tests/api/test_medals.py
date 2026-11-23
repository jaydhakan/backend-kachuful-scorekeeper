"""`GET /api/medals`: Kachuful's month without the game at the table (rewards plan §6.7)."""

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload
from tests.placed_games import split_payload

HOUR = 60 * MINUTE


def _rows(body: dict[str, Any]) -> list[tuple[str, int | None, int, int]]:
    return [(r["player"]["id"], r["rank"], r["rounds"], r["points"]) for r in body["standings"]]


async def test_medals_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/medals")).status_code == 401


async def test_medals_leave_out_the_game_at_the_table(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, player_name="Scorer")
    await sign_in(client, user)
    ids = [str((await make_player(session, name=f"M{k}")).id) for k in range(3)]
    now = to_ms(now_utc())
    earlier = make_payload(ids, start_ms=now - 2 * HOUR, rounds=55, seed=3)
    assert (await client.put(f"/api/games/{earlier['syncId']}", json=earlier)).status_code == 200
    before = (await client.get("/api/medals")).json()

    table = make_payload(ids, start_ms=now - 40 * MINUTE, rounds=10, seed=4, finished=False)
    assert (await client.put(f"/api/games/{table['syncId']}", json=table)).status_code == 200
    with_it = (await client.get("/api/medals")).json()
    without = (await client.get("/api/medals", params={"excludeGame": table["syncId"]})).json()
    unknown = (await client.get("/api/medals", params={"excludeGame": str(uuid.uuid4())})).json()

    assert before["minRounds"] == 50
    assert before["season"] == with_it["season"]
    assert all(r["qualified"] and r["rounds"] == 55 for r in before["standings"])
    assert all(r["rounds"] == 65 for r in with_it["standings"])
    assert _rows(without) == _rows(before)
    assert _rows(unknown) == _rows(with_it)
    assert before["lastSeason"] == []
    # Tonight without the game at the table: only the earlier game counts.
    assert without["today"]["games"] == 1
    assert with_it["today"]["games"] == 2
    assert {r["player"]["id"]: r["rounds"] for r in without["today"]["rows"]} == dict.fromkeys(
        ids, 55
    )


async def test_medals_rank_by_accuracy_without_the_tables_bids(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, player_name="Scorer")
    await sign_in(client, user)
    ids = [str((await make_player(session, name=f"A{k}")).id) for k in range(3)]
    now = to_ms(now_utc())
    earlier, sharpest, _ = split_payload(ids, start_ms=now - 3 * HOUR)
    assert (await client.put(f"/api/games/{earlier['syncId']}", json=earlier)).status_code == 200
    before = (await client.get("/api/medals")).json()

    table = make_payload(ids, start_ms=now - 40 * MINUTE, rounds=10, seed=4, finished=False)
    assert (await client.put(f"/api/games/{table['syncId']}", json=table)).status_code == 200
    without = (await client.get("/api/medals", params={"excludeGame": table["syncId"]})).json()

    assert before["standings"][0]["player"]["id"] == sharpest
    assert [r["value"] for r in before["standings"]] == [r["accuracy"] for r in before["standings"]]
    # The table's made bids leave with its rounds and points.
    assert [(r["rank"], r["value"], r["accuracy"]) for r in without["standings"]] == [
        (r["rank"], r["value"], r["accuracy"]) for r in before["standings"]
    ]


async def test_medals_drop_a_player_whose_only_rounds_are_the_excluded_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, player_name="Scorer")
    await sign_in(client, user)
    ids = [str((await make_player(session, name=f"N{k}")).id) for k in range(3)]
    table = make_payload(ids, start_ms=to_ms(now_utc()) - HOUR, rounds=5, seed=5, finished=False)
    assert (await client.put(f"/api/games/{table['syncId']}", json=table)).status_code == 200

    body = (await client.get("/api/medals", params={"excludeGame": table["syncId"]})).json()

    assert body["standings"] == []


async def test_places_list_ended_games_newest_first_without_the_excluded_one(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, player_name="Scorer")
    await sign_in(client, user)
    ids = [str((await make_player(session, name=f"P{k}")).id) for k in range(3)]
    now = to_ms(now_utc())
    older = make_payload(ids, start_ms=now - 3 * HOUR, rounds=4, seed=2)
    newer = make_payload(ids, start_ms=now - HOUR, rounds=4, seed=3)
    for p in (older, newer):
        assert (await client.put(f"/api/games/{p['syncId']}", json=p)).status_code == 200

    body = (await client.get("/api/places")).json()
    without = (await client.get("/api/places", params={"excludeGame": newer["syncId"]})).json()

    assert [g["gameId"] for g in body["games"]] == [newer["syncId"], older["syncId"]]
    assert sorted(s["playerId"] for s in body["games"][0]["seats"]) == sorted(ids)
    assert [g["gameId"] for g in without["games"]] == [older["syncId"]]
