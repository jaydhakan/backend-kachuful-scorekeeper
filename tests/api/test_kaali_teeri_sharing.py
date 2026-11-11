"""Live and recap links for Kaali Teeri games: same routes as Kachuful, bodies carry `kind`."""

import copy
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.public import load_live_event
from tests.factories import make_user, sign_in
from tests.kt_builder import fixture_payload, freeze_clock


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)


async def _game(
    client: httpx.AsyncClient, session: AsyncSession, *, finished: bool = True
) -> dict[str, Any]:
    owner = await make_user(session, email="scorer@example.com", player_name="Scorer")
    await sign_in(client, owner)
    payload, _ = await fixture_payload(session, "kt-4p")
    if not finished:
        payload = copy.deepcopy(payload) | {"status": "active"}
        payload.pop("finishedAt")
    response = await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return payload


async def _link(client: httpx.AsyncClient, game_id: str, kind: str) -> dict[str, Any]:
    response = await client.post(f"/api/games/{game_id}/links", json={"kind": kind})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_live_link_shows_the_table_with_its_kind(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session, finished=False)
    link = await _link(client, payload["syncId"], "live")

    client.cookies.clear()
    body = (await client.get(f"/api/public/live/{link['token']}")).json()

    assert body["kind"] == "kaali-teeri" and body["game"] == payload
    assert body["summary"]["kind"] == "kaali-teeri" and body["summary"]["hands"] == 4
    assert body["summary"]["owner"]["id"] is None
    assert link["url"] == f"http://test/l/{link['token']}"


async def test_recap_link_shows_the_game_without_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    link = await _link(client, payload["syncId"], "recap")

    client.cookies.clear()
    body = (await client.get(f"/api/public/recap/{link['token']}")).json()
    image = await client.get(f"/api/public/recap/{link['token']}/og.png")

    assert body["kind"] == "kaali-teeri" and body["achievements"] == []
    assert body["summary"]["status"] == "finished"
    assert all(p["place"] is not None for p in body["summary"]["players"])
    assert image.status_code == 200 and image.content[:4] == b"\x89PNG"


async def test_preview_pages_name_the_game_and_open_its_page(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session, finished=False)
    live = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")

    live_page = (await client.get(f"/l/{live['token']}")).text
    recap_page = (await client.get(f"/r/{recap['token']}")).text

    assert f'content="0;url=/kaali-teeri?live={live["token"]}"' in live_page
    assert f'content="0;url=/kaali-teeri?recap={recap["token"]}"' in recap_page
    assert 'property="og:site_name" content="Baithak"' in live_page
    assert "Kaali Teeri" in live_page and "4 hands" in recap_page
    assert "Open in Baithak" in recap_page


async def test_a_live_link_of_a_long_finished_game_has_expired(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # kt-4p finished on 2026-11-02, well over two hours ago.
    payload = await _game(client, session)
    link = await _link(client, payload["syncId"], "live")

    response = await client.get(f"/api/public/live/{link['token']}")

    assert link["expiresAt"] == payload["finishedAt"] + 2 * 3600 * 1000
    assert (response.status_code, response.json()["code"]) == (410, "gone")


async def test_deleting_the_game_switches_its_links_off(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session, finished=False)
    live = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")

    await client.delete(f"/api/kaali-teeri/games/{payload['syncId']}")

    gone_live = await client.get(f"/api/public/live/{live['token']}")
    gone_recap = await client.get(f"/api/public/recap/{recap['token']}")
    assert (gone_live.status_code, gone_recap.status_code) == (410, 410)
    assert (
        await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
    ).status_code == 404


async def test_live_events_carry_the_kaali_teeri_body(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session, finished=False)
    await _link(client, payload["syncId"], "live")

    event = await load_live_event(uuid.UUID(payload["syncId"]))

    assert event is not None
    rev, data = event
    assert rev == payload["rev"] and json.loads(data)["kind"] == "kaali-teeri"


async def test_only_the_scorer_or_an_admin_may_share(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    await sign_in(client, await make_user(session, email="other@example.com", player_name="O"))

    response = await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "recap"})

    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
