"""Spades online with real commits: each follower's frames, presence, reactions, the end, and
actions racing on one table."""

import asyncio
import json
import time
import uuid
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from app.db.models import Player, User
from app.db.session import get_session_factory
from app.live.hub import hub
from app.security import rate_limit
from app.services.spades_store import stamp_table_seen
from tests.api.test_ishaara_live import _client, _frames
from tests.api.test_live_events import _next_event
from tests.factories import make_user
from tests.spades_tables_builder import (
    NAMES,
    RULES,
    SUITS,
    Clock,
    Seats,
    freeze,
    ok,
    seated,
    view,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return freeze(monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_reactions() -> None:
    rate_limit.SPADES_REACTION._hits.clear()


async def _users(extra: int = 0) -> list[User]:
    """Four committed accounts (and `extra` strangers), the others created by the first."""
    async with get_session_factory()() as session:
        names = [*NAMES, *(f"Watcher {k}" for k in range(extra))]
        users = [
            await make_user(session, email=f"live{k}@example.com", player_name=n)
            for k, n in enumerate(names)
        ]
        for u in users[1:4]:
            await session.execute(
                update(Player).where(Player.id == u.player_id).values(created_by=users[0].id)
            )
        await session.commit()
    return users


async def _seats(
    stack: AsyncExitStack, base_url: str | None, extra: int = 0
) -> tuple[Seats, list[httpx.AsyncClient]]:
    users = await _users(extra)
    clients = [await _client(stack, u, base_url) for u in users]
    return Seats(users=users[:4], clients=clients[:4]), clients[4:]


def _data(event: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(event["data"])
    return out


async def _stream(stack: AsyncExitStack, client: httpx.AsyncClient, path: str) -> Any:
    stream = await stack.enter_async_context(client.stream("GET", f"{path}/events"))
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    return _frames(stream)


async def _state_where(events: Any, check: Any) -> dict[str, Any]:
    """The next `state` frame that passes `check`."""
    async with asyncio.timeout(5):
        while True:
            frame = _data(await _next_event(events, "state"))
            if check(frame):
                return frame


async def test_each_follower_gets_only_its_own_hand(live_server: str, clock: Clock) -> None:
    async with AsyncExitStack() as stack:
        seats, (watcher,) = await _seats(stack, live_server, extra=1)
        await seated(seats)
        await ok(seats, 0, "drawForDeal")
        streams = [await _stream(stack, c, seats.path) for c in [*seats.clients, watcher]]
        for events in streams:
            await _next_event(events, "state")
        await ok(seats, 3, "deal")
        frames = [
            await _state_where(e, lambda f: f["table"]["phase"] == "bidding") for e in streams
        ]

    for seat, frame in enumerate(frames[:4]):
        assert frame["me"]["seat"] == seat
        assert {c["suit"] for c in frame["me"]["hand"]} == {SUITS[seat]}
        public = {k: v for k, v in frame["table"].items() if k != "drawn"}
        assert '"rank"' not in json.dumps(public)
    assert frames[4]["me"] == {"seat": None, "host": False, "hand": None}
    assert '"rank"' not in json.dumps({k: v for k, v in frames[4]["table"].items() if k != "drawn"})


async def test_an_early_blind_nil_shows_only_to_its_seat(live_server: str, clock: Clock) -> None:
    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, live_server)
        await seated(seats, blindNil=True)
        await ok(seats, 0, "drawForDeal")
        await ok(seats, 3, "deal")
        streams = [await _stream(stack, c, seats.path) for c in seats.clients]
        for events in streams:
            await _next_event(events, "state")
        await ok(seats, 2, "blindNil")
        frames = [await _state_where(e, lambda f: True) for e in streams]

    assert frames[2]["table"]["blind"] == [False, False, True, False]
    for seat in (0, 1, 3):
        assert frames[seat]["table"]["blind"] == [False, False, False, False]
        assert frames[seat]["me"]["hand"] is None


async def test_presence_reactions_and_the_end_reach_every_follower(
    live_server: str, clock: Clock
) -> None:
    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, live_server)
        await seated(seats)
        host = await _stream(stack, seats.clients[0], seats.path)
        await _next_event(host, "state")
        alone = _data(await _next_event(host, "seat-presence"))
        await _stream(stack, seats.clients[2], seats.path)
        both = _data(await _next_event(host, "seat-presence"))
        await ok(seats, 1, "react", emoji="🔥")
        reaction = _data(await _next_event(host, "reaction"))
        await ok(seats, 0, "cancelGame")
        ended = _data(await _next_event(host, "ended"))

    assert alone == {"seats": [True, False, False, False]}
    assert both == {"seats": [True, False, True, False]}
    assert reaction == {"seat": 1, "emoji": "🔥"}
    assert ended == {"status": "cancelled", "result": None}


async def test_an_invite_shows_on_the_lobby_stream(live_server: str, clock: Clock) -> None:
    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, live_server)
        response = await seats.clients[0].post(
            "/api/spades/tables", json={"id": str(uuid.uuid4()), "rules": RULES}
        )
        seats.table = response.json()["table"]["id"]
        host = await _stream(stack, seats.clients[0], seats.path)
        await _next_event(host, "state")
        await seats.clients[0].post(
            f"{seats.path}/seat-invites", json={"userId": str(seats.users[2].id), "seat": "2"}
        )
        frame = await _state_where(host, lambda f: f["invites"][2] is not None)

    assert frame["invites"][2]["userId"] == str(seats.users[2].id)
    assert frame["invites"][2]["name"] == "Dev"


async def test_two_plays_racing_apply_once(
    committed_client: httpx.AsyncClient, clock: Clock
) -> None:
    async with AsyncExitStack() as stack:
        seats, (twin,) = await _twin(stack)
        await seated(seats)
        await ok(seats, 0, "drawForDeal")
        await ok(seats, 3, "deal")
        for seat, bid in ((0, 5), (1, 3), (2, 3), (3, 2)):
            await ok(seats, seat, "bid", bid=bid)
        table = (await view(seats, 0))["table"]
        hand = (await view(seats, 0))["me"]["hand"]
        path = f"{seats.path}/actions"

        def body(card: dict[str, Any]) -> dict[str, Any]:
            return {
                "actionId": str(uuid.uuid4()),
                "rev": table["rev"],
                "type": "play",
                "card": card,
            }

        results = await asyncio.gather(
            seats.clients[0].post(path, json=body(hand[0])),
            twin.post(path, json=body(hand[1])),
        )
        after = await view(seats, 0)

    assert sorted(r.status_code for r in results) == [200, 409]
    stale = next(r for r in results if r.status_code == 409).json()
    assert stale["code"] == "stale" and stale["game"]["table"]["rev"] == table["rev"] + 1
    assert after["table"]["rev"] == table["rev"] + 1 and len(after["table"]["trick"]) == 1
    assert len(after["me"]["hand"]) == 12


async def _twin(stack: AsyncExitStack) -> tuple[Seats, tuple[httpx.AsyncClient]]:
    """Four seats over committed clients, plus a second client signed in as seat 0."""
    seats, _ = await _seats(stack, None)
    return seats, (await _client(stack, seats.users[0]),)


async def test_timeouts_racing_settle_the_turn_once(
    committed_client: httpx.AsyncClient, clock: Clock
) -> None:
    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, None)
        await seated(seats)
        await ok(seats, 0, "drawForDeal")
        await ok(seats, 3, "deal")
        rev = (await view(seats, 0))["table"]["rev"]
        clock.advance(31)
        path = f"{seats.path}/actions"

        def body() -> dict[str, Any]:
            return {"actionId": str(uuid.uuid4()), "rev": rev, "type": "timeout"}

        results = await asyncio.gather(
            *(seats.clients[s].post(path, json=body()) for s in (1, 2, 3))
        )
        after = (await view(seats, 0))["table"]

    assert [r.status_code for r in results] == [200, 200, 200]
    assert after["rev"] == rev + 1 and after["bids"][0] == 1 and after["turn"] == 1


async def test_every_follower_sees_an_action_within_a_second(
    live_server: str, clock: Clock
) -> None:
    async with AsyncExitStack() as stack:
        seats, watchers = await _seats(stack, live_server, extra=3)
        await seated(seats)
        await ok(seats, 0, "drawForDeal")
        streams = [await _stream(stack, c, seats.path) for c in [*seats.clients, *watchers]]
        for events in streams:
            await _next_event(events, "state")

        started = time.monotonic()
        await ok(seats, 3, "deal")
        await asyncio.wait_for(
            asyncio.gather(
                *(_state_where(e, lambda f: f["table"]["phase"] == "bidding") for e in streams)
            ),
            1.0,
        )

    assert time.monotonic() - started < 1.0


async def test_followers_catch_up_when_the_listen_connection_dies(
    live_server: str, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconnect = hub._ensure_listening

    async def slow_reconnect() -> None:
        await asyncio.sleep(0.5)  # the deal below commits while nobody is listening
        await reconnect()

    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, live_server)
        await seated(seats)
        await ok(seats, 0, "drawForDeal")
        streams = [await _stream(stack, c, seats.path) for c in seats.clients]
        for events in streams:
            await _next_event(events, "state")

        assert hub._connection is not None
        monkeypatch.setattr(hub, "_ensure_listening", slow_reconnect)
        hub._connection.terminate()
        # Whatever is committed while the hub is deaf is delivered by the resync after reconnecting.
        await ok(seats, 3, "deal")
        frames = [
            await _state_where(e, lambda f: f["table"]["phase"] == "bidding") for e in streams
        ]

    assert all(f["table"]["phase"] == "bidding" for f in frames)


async def test_the_ping_rev_is_the_tables_current_rev(live_server: str, clock: Clock) -> None:
    async with AsyncExitStack() as stack:
        seats, _ = await _seats(stack, live_server)
        await seated(seats)
        user_id = seats.users[0].id
        game_id = uuid.UUID(seats.path.rsplit("/", 1)[1])

        rev = await stamp_table_seen(game_id, user_id)
        table = (await seats.clients[0].get(seats.path)).json()["table"]

    assert rev == table["rev"]
