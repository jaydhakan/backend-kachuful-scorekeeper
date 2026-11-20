"""`GET /api/medals`: Kachuful's month without the game at the table (rewards plan §6.7)."""

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload

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
