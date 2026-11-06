"""SSE (plan §12.2): a connected viewer receives each upload, then `revoked` when switched off."""

import asyncio
import copy
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.db.session import get_session_factory
from app.domain.time import now_utc, to_ms
from tests.conftest import WRITE_HEADERS
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload


async def _events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    event: dict[str, Any] = {}
    async for line in lines:
        if not line:
            if event:
                yield event
            event = {}
        elif ":" in line:
            field, value = line.split(":", 1)
            event[field] = value.strip()


async def _next_event(events: AsyncIterator[dict[str, Any]], kind: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        async for event in events:
            if event.get("event") == kind:
                return event
    raise AssertionError(f"no {kind} event")


async def test_viewer_gets_each_upload_and_then_revoked(live_server: str) -> None:
    async with get_session_factory()() as session:
        owner = await make_user(session, email="live@example.com", player_name="Host")
        players = [await make_player(session, name=f"L{k}") for k in range(3)]
        await session.commit()
    async with httpx.AsyncClient(base_url=live_server, headers=WRITE_HEADERS) as scorer:
        await sign_in(scorer, owner)
        payload = make_payload(
            [str(p.id) for p in players],
            start_ms=to_ms(now_utc()) - 30 * MINUTE,
            finished=False,
            rounds=3,
        )
        await scorer.put(f"/api/games/{payload['syncId']}", json=payload)
        link = (
            await scorer.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
        ).json()

        async with (
            httpx.AsyncClient(base_url=live_server, timeout=10) as viewer,
            viewer.stream("GET", f"/api/public/live/{link['token']}/events") as stream,
        ):
            assert stream.headers["content-type"].startswith("text/event-stream")
            events = _events(stream.aiter_lines())
            first = await _next_event(events, "game")
            assert first["id"] == "1"
            assert json.loads(first["data"])["summary"]["owner"]["id"] is None

            newer = copy.deepcopy(payload)
            newer["rev"] = 2
            newer["rounds"] = make_payload(
                [str(p.id) for p in players],
                start_ms=payload["createdAt"],
                finished=False,
                rounds=4,
                game_id=payload["syncId"],
            )["rounds"]
            newer["updatedAt"] = newer["rounds"][-1]["doneAt"]
            sent = time.perf_counter()
            await scorer.put(f"/api/games/{payload['syncId']}", json=newer)
            update = await _next_event(events, "game")
            latency_ms = (time.perf_counter() - sent) * 1000
            assert update["id"] == "2"
            assert json.loads(update["data"])["summary"]["rounds"] == 4
            assert latency_ms < 1000

            await scorer.delete(f"/api/games/{payload['syncId']}/links/live")
            await _next_event(events, "revoked")


async def test_reconnecting_with_the_current_rev_skips_the_first_event(live_server: str) -> None:
    async with get_session_factory()() as session:
        owner = await make_user(session, email="live2@example.com", player_name="Host2")
        players = [await make_player(session, name=f"R{k}") for k in range(3)]
        await session.commit()
    async with httpx.AsyncClient(base_url=live_server, headers=WRITE_HEADERS) as scorer:
        await sign_in(scorer, owner)
        payload = make_payload(
            [str(p.id) for p in players], start_ms=to_ms(now_utc()) - 30 * MINUTE
        )
        await scorer.put(f"/api/games/{payload['syncId']}", json=payload)
        link = (
            await scorer.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
        ).json()
        async with (
            httpx.AsyncClient(base_url=live_server, timeout=10) as viewer,
            viewer.stream(
                "GET",
                f"/api/public/live/{link['token']}/events",
                headers={"Last-Event-ID": "1"},
            ) as stream,
        ):
            events = _events(stream.aiter_lines())
            await scorer.delete(f"/api/games/{payload['syncId']}/links/live")
            async with asyncio.timeout(5):
                first = await anext(events)
                # The watcher count is not a game state; what matters is that no `game` comes first.
                while first["event"] == "watchers":
                    first = await anext(events)
            assert first["event"] == "revoked"


async def test_viewers_see_how_many_watch_and_each_others_reactions(live_server: str) -> None:
    async with get_session_factory()() as session:
        owner = await make_user(session, email="cheer@example.com", player_name="Cheer")
        players = [await make_player(session, name=f"C{k}") for k in range(3)]
        await session.commit()
    async with httpx.AsyncClient(base_url=live_server, headers=WRITE_HEADERS) as scorer:
        await sign_in(scorer, owner)
        payload = make_payload(
            [str(p.id) for p in players],
            start_ms=to_ms(now_utc()) - 30 * MINUTE,
            finished=False,
            rounds=2,
        )
        await scorer.put(f"/api/games/{payload['syncId']}", json=payload)
        link = (
            await scorer.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
        ).json()
        token = link["token"]
        async with (
            httpx.AsyncClient(base_url=live_server, timeout=10, headers=WRITE_HEADERS) as viewer,
            viewer.stream("GET", f"/api/public/live/{token}/events") as stream,
        ):
            events = _events(stream.aiter_lines())
            await _next_event(events, "game")
            watchers = await _next_event(events, "watchers")
            assert json.loads(watchers["data"]) == {"n": 1}
            sent = await viewer.post(f"/api/public/live/{token}/reactions", json={"kind": "party"})
            assert sent.status_code == 204
            reaction = await _next_event(events, "reaction")
            assert json.loads(reaction["data"]) == {"kind": "party"}
