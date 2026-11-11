"""Ishaara with real commits: concurrent picks on one game, and the events stream."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import httpx

from app.bootstrap.app import create_app
from app.db.models import User
from app.db.session import get_session_factory
from tests.api.test_live_events import _events, _next_event
from tests.conftest import ORIGIN, WRITE_HEADERS
from tests.factories import make_player, make_user, sign_in
from tests.ishaara_builder import TEAM_WORDS, Table


async def _committed_table() -> Table:
    async with get_session_factory()() as session:
        host = await make_user(session, email="host@example.com", player_name="Host")
        sun = await make_user(session, email="sun@example.com", player_name="Sunny")
        moon = await make_user(session, email="moon@example.com", player_name="Luna")
        guests = [await make_player(session, name=f"Guest {k}") for k in range(2)]
        await session.commit()
    return Table(host=host, sun=sun, moon=moon, guests=guests)


async def _client(
    stack: AsyncExitStack, user: User, base_url: str | None = None
) -> httpx.AsyncClient:
    if base_url is None:
        transport = httpx.ASGITransport(app=create_app(), client=("203.0.113.10", 50000))
        client = httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=WRITE_HEADERS)
    else:
        client = httpx.AsyncClient(base_url=base_url, headers=WRITE_HEADERS, timeout=10)
    await stack.enter_async_context(client)
    await sign_in(client, user)
    return client


def _body(state: dict[str, Any], type_: str, **fields: Any) -> dict[str, Any]:
    return {"actionId": str(uuid.uuid4()), "rev": state["rev"], "type": type_, **fields}


async def _started(table: Table) -> tuple[dict[str, Any], list[str]]:
    created = await table.clients["host"].post("/api/ishaara/games", json=table.body())
    assert created.status_code == 201, created.text
    game_id = created.json()["game"]["id"]
    key: list[str] = []
    for team in ("sun", "moon"):
        key = (await table.clients[team].get(f"/api/ishaara/games/{game_id}/key")).json()["key"]
    state = (await table.clients["host"].get(f"/api/ishaara/games/{game_id}")).json()["game"]
    team = state["turn"]
    clued = await table.clients[team].post(
        f"/api/ishaara/games/{game_id}/actions",
        json=_body(state, "clue", word=TEAM_WORDS[team], number="inf"),
    )
    assert clued.status_code == 200, clued.text
    return clued.json()["game"], key


async def test_two_picks_at_the_same_rev_apply_once(committed_client: httpx.AsyncClient) -> None:
    table = await _committed_table()
    async with AsyncExitStack() as stack:
        await sign_in(committed_client, table.host)
        table.clients = {
            "host": committed_client,
            "sun": await _client(stack, table.sun),
            "moon": await _client(stack, table.moon),
        }
        second_host = await _client(stack, table.host)
        state, key = await _started(table)
        first, other = [i for i, k in enumerate(key) if k == state["turn"]][:2]
        path = f"/api/ishaara/games/{state['id']}/actions"

        results = await asyncio.gather(
            committed_client.post(path, json=_body(state, "guess", card=first)),
            second_host.post(path, json=_body(state, "guess", card=other)),
        )

        assert sorted(r.status_code for r in results) == [200, 409]
        stale = next(r for r in results if r.status_code == 409).json()
        assert stale["code"] == "stale" and stale["game"]["game"]["rev"] == state["rev"] + 1
        now = (await committed_client.get(f"/api/ishaara/games/{state['id']}")).json()["game"]
        assert now["rev"] == state["rev"] + 1
        assert sum(r is not None for r in now["revealed"]) == 1


async def _frames(stream: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for event in _events(stream.aiter_lines()):
        yield event


async def test_followers_get_state_joined_and_ended_and_never_the_key(live_server: str) -> None:
    table = await _committed_table()
    async with AsyncExitStack() as stack:
        table.clients = {
            name: await _client(stack, user, live_server)
            for name, user in (("host", table.host), ("sun", table.sun), ("moon", table.moon))
        }
        stranger_user = await _stranger()
        stranger = await _client(stack, stranger_user, live_server)
        created = await table.clients["host"].post("/api/ishaara/games", json=table.body())
        game_id = created.json()["game"]["id"]
        path = f"/api/ishaara/games/{game_id}/events"

        refused = await stranger.get(path)
        stream = await stack.enter_async_context(table.clients["sun"].stream("GET", path))
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["x-accel-buffering"] == "no"
        events = _frames(stream)
        first = await _next_event(events, "state")
        key = (await table.clients["sun"].get(f"/api/ishaara/games/{game_id}/key")).json()["key"]
        after_join = await _next_event(events, "state")
        joined = await _next_event(events, "joined")
        await table.clients["moon"].get(f"/api/ishaara/games/{game_id}/key")
        active = await _next_event(events, "state")
        state = json.loads(active["data"])["game"]
        await table.clients["host"].post(
            f"/api/ishaara/games/{game_id}/actions", json=_body(state, "resign", team="sun")
        )
        final = await _next_event(events, "state")
        ended = await _next_event(events, "ended")

    assert refused.status_code == 404
    assert first["id"] == "1" and json.loads(first["data"])["game"]["status"] == "waiting"
    assert "serverNow" in json.loads(first["data"])
    assert after_join["id"] == "2" and json.loads(joined["data"]) == {"team": "sun"}
    assert json.loads(active["data"])["game"]["status"] == "active"
    compact = ",".join(f'"{k}"' for k in key)
    for frame in (first, after_join, joined, active):
        assert compact not in frame["data"].replace(" ", "")
        assert json.loads(frame["data"]).get("game", {}).get("solution") is None
    assert json.loads(final["data"])["game"]["solution"] == key
    assert json.loads(ended["data"]) == {"winner": "moon", "endedBy": "resign"}


async def _stranger() -> User:
    async with get_session_factory()() as session:
        user = await make_user(session, email="who@example.com", player_name="Who")
        await session.commit()
    return user
