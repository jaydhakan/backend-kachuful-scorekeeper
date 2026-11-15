"""`GET /api/me/events` with real commits: each user gets only their own invite events, and the
stream's pings mark the user seen."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest

from app.db.models import User
from app.db.session import get_session_factory
from app.live import sse
from tests.api.test_live_events import _events, _next_event
from tests.conftest import WRITE_HEADERS
from tests.factories import make_player, make_user, sign_in
from tests.ishaara_builder import Table


async def _committed_table() -> tuple[Table, User]:
    async with get_session_factory()() as session:
        host = await make_user(session, email="host@example.com", player_name="Host")
        sun = await make_user(session, email="sun@example.com", player_name="Sunny")
        moon = await make_user(session, email="moon@example.com", player_name="Luna")
        guests = [await make_player(session, name=f"Guest {k}") for k in range(2)]
        stranger = await make_user(session, email="who@example.com", player_name="Who")
        await session.commit()
    return Table(host=host, sun=sun, moon=moon, guests=guests), stranger


async def _client(stack: AsyncExitStack, base_url: str, user: User) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base_url, headers=WRITE_HEADERS, timeout=10)
    await stack.enter_async_context(client)
    await sign_in(client, user)
    return client


async def _stream(
    stack: AsyncExitStack, client: httpx.AsyncClient, headers: dict[str, str] | None = None
) -> AsyncIterator[dict[str, Any]]:
    response = await stack.enter_async_context(
        client.stream("GET", "/api/me/events", headers=headers)
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    return _events(response.aiter_lines())


async def _until_ping(events: AsyncIterator[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    async with asyncio.timeout(5):
        async for event in events:
            if event.get("event") == "ping":
                return seen
            seen.append(str(event.get("event")))
    raise AssertionError("no ping")


def _data(event: dict[str, Any]) -> Any:
    return json.loads(event["data"])


async def test_each_user_gets_only_their_own_invite_events(
    live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse, "PING_EVERY_S", 0.5)
    table, stranger = await _committed_table()
    async with AsyncExitStack() as stack:
        clients = {
            name: await _client(stack, live_server, user)
            for name, user in (
                ("host", table.host),
                ("sun", table.sun),
                ("moon", table.moon),
                ("who", stranger),
            )
        }
        streams = {name: await _stream(stack, client) for name, client in clients.items()}
        for events in streams.values():
            assert "serverNow" in _data(await _next_event(events, "hello"))

        created = await clients["host"].post("/api/ishaara/games", json=table.body())
        assert created.status_code == 201, created.text
        game_id = created.json()["game"]["id"]
        to_sun = _data(await _next_event(streams["sun"], "seat-invite"))
        to_moon = _data(await _next_event(streams["moon"], "seat-invite"))
        updates = [_data(await _next_event(streams["host"], "seat-invite-update")) for _ in "ab"]
        stranger_saw = await _until_ping(streams["who"])
        sun_before_ping = await _until_ping(streams["sun"])

        await clients["sun"].post(f"/api/seat-invites/{to_sun['id']}/decline")
        gone = _data(await _next_event(streams["sun"], "seat-invite-gone"))
        declined = _data(await _next_event(streams["host"], "seat-invite-update"))

    assert (to_sun["seat"], to_sun["gameId"], to_sun["status"]) == ("clue:sun", game_id, "pending")
    assert to_sun["from"]["userId"] == str(table.host.id)
    assert to_moon["seat"] == "clue:moon" and to_moon["id"] != to_sun["id"]
    assert sorted((u["seat"], u["status"], u["gameId"]) for u in updates) == [
        ("clue:moon", "pending", game_id),
        ("clue:sun", "pending", game_id),
    ]
    assert stranger_saw == [] and sun_before_ping == []
    assert gone == {"id": to_sun["id"], "status": "declined"}
    assert declined == {
        "id": to_sun["id"],
        "status": "declined",
        "gameId": game_id,
        "seat": "clue:sun",
    }


async def test_opening_and_pings_mark_the_user_seen(
    live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse, "PING_EVERY_S", 0.2)
    _, user = await _committed_table()
    async with AsyncExitStack() as stack:
        client = await _client(stack, live_server, user)
        events = await _stream(stack, client, headers={"Last-Event-ID": "not-a-number"})
        hello = _data(await _next_event(events, "hello"))
        opened = await _seen(user)
        await _next_event(events, "ping")
        await _next_event(events, "ping")
        pinged = await _seen(user)

    assert opened is not None and pinged is not None
    assert int(opened.timestamp() * 1000) == hello["serverNow"]
    assert pinged > opened


async def _seen(user: User) -> Any:
    async with get_session_factory()() as session:
        fresh = await session.get(User, user.id, populate_existing=True)
        assert fresh is not None
        return fresh.last_seen_at


async def test_signed_out_cannot_open_the_stream(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/me/events")

    assert (response.status_code, response.json()["code"]) == (401, "unauthenticated")
