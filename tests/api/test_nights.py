"""`GET /api/nights`, `GET /api/nights/{id}` and `POST /api/nights/close`."""

import argparse
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import now_utc, to_ms
from app.jobs import close_nights
from app.jobs.fake_games import MINUTE
from app.services.nights import night_uuid
from tests.factories import make_player, make_user, sign_in
from tests.placed_games import placed_payload

HOUR = 60 * MINUTE


async def _table(client: httpx.AsyncClient, session: AsyncSession) -> list[str]:
    user = await make_user(session, email="a@example.com", player_name="Owl")
    await sign_in(client, user)
    assert user.player_id is not None
    others = [str((await make_player(session, name=f"W{k}")).id) for k in range(2)]
    return [str(user.player_id), *others]


async def _night(
    client: httpx.AsyncClient, ids: list[str], hours_ago: float
) -> list[dict[str, Any]]:
    now = to_ms(now_utc())
    games = [
        placed_payload(ids, start_ms=now - int(hours_ago * HOUR)),
        placed_payload(ids, start_ms=now - int((hours_ago - 1) * HOUR)),
    ]
    for g in games:
        response = await client.put(f"/api/games/{g['syncId']}", json=g)
        assert response.status_code == 200, response.text
    return games


def _ids(players: list[dict[str, Any]]) -> list[str]:
    return [p["id"] for p in players]


async def test_nights_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/nights")).status_code == 401
    assert (await client.get(f"/api/nights/{uuid.uuid4()}")).status_code == 401
    assert (await client.post("/api/nights/close")).status_code == 401


async def test_the_open_night_comes_first_with_no_id(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    games = await _night(client, ids, 2)

    body = (await client.get("/api/nights")).json()

    assert body["nextCursor"] is None
    (item,) = body["items"]
    assert item["id"] is None and item["closed"] is False
    assert item["startedAt"] == games[0]["createdAt"] and item["endedAt"] == games[1]["finishedAt"]
    assert item["games"] == 2
    assert _ids(item["players"])[0] == ids[0] and item["winner"]["id"] == ids[0]


async def test_closed_nights_list_newest_first_and_page(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    older = await _night(client, ids, 30)
    newer = await _night(client, ids, 8)
    await close_nights.run(argparse.Namespace(all=False))

    everything = (await client.get("/api/nights", params={"playerId": ids[1]})).json()
    first = (await client.get("/api/nights", params={"limit": 1})).json()
    rest = (
        await client.get("/api/nights", params={"limit": 1, "cursor": first["nextCursor"]})
    ).json()

    assert [i["id"] for i in everything["items"]] == [
        str(night_uuid(newer[0]["syncId"])),
        str(night_uuid(older[0]["syncId"])),
    ]
    assert all(i["closed"] for i in everything["items"])
    assert first["items"] + rest["items"] == everything["items"]
    assert rest["nextCursor"] is None


async def test_a_night_reads_with_its_story(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    games = await _night(client, ids, 8)
    await close_nights.run(argparse.Namespace(all=False))
    night_id = night_uuid(games[0]["syncId"])

    body = (await client.get(f"/api/nights/{night_id}")).json()

    assert body["id"] == str(night_id) and body["closed"] is True
    assert _ids(body["players"])[0] == ids[0]
    assert body["data"]["gameIds"] == [g["syncId"] for g in games]
    assert body["data"]["sitara"] == [ids[0]]


async def test_closing_my_night_now(client: httpx.AsyncClient, session: AsyncSession) -> None:
    ids = await _table(client, session)
    games = await _night(client, ids, 2)

    response = await client.post("/api/nights/close")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(night_uuid(games[0]["syncId"])) and body["closed"] is True
    assert body["data"]["winner"] == ids[0]
    (item,) = (await client.get("/api/nights")).json()["items"]
    assert item["id"] == body["id"] and item["closed"] is True
    assert (await client.post("/api/nights/close")).status_code == 404


async def test_errors(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await _table(client, session)

    no_night = await client.post("/api/nights/close")
    unknown = await client.get(f"/api/nights/{uuid.uuid4()}")
    stranger = await client.get("/api/nights", params={"playerId": str(uuid.uuid4())})
    bad = await client.get("/api/nights", params={"cursor": "nope"})

    assert (no_night.status_code, no_night.json()["code"]) == (404, "not_found")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "not_found")
    assert (stranger.status_code, stranger.json()["code"]) == (404, "not_found")
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid")
    assert (await client.get("/api/nights")).json() == {"items": [], "nextCursor": None}


async def test_an_account_with_no_player_has_no_nights(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="np@example.com", player_name=None)
    await sign_in(client, user)

    assert (await client.get("/api/nights")).json() == {"items": [], "nextCursor": None}
    assert (await client.post("/api/nights/close")).status_code == 404
