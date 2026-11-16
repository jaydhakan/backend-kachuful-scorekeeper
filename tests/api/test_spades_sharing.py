"""Live and recap links for both Spades kinds: the generic link routes, bodies carry `kind`.

An online table's link shows the table as no seat sees it: never a held card, never an account id,
and never a Blind Nil declared ahead of its bid.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.public import load_live_event
from tests.factories import make_user, sign_in
from tests.spades_builder import (
    case,
    finished_state,
    freeze_clock,
    replay,
    seat_registry,
    store_table,
)

# spades-nil-blind after 16 steps: seat 2 has declared Blind Nil before its turn to bid, and
# seats 0 and 1 have looked at their cards.
HIDDEN_BLIND_STEPS = 16
ACCOUNTS = ("u-riya", "u-arjun", "u-dev", "u-meera")


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)


async def _owner(client: httpx.AsyncClient, session: AsyncSession) -> User:
    await seat_registry(session)
    owner = await make_user(session, email="scorer@example.com", player_name="Scorer")
    await sign_in(client, owner)
    return owner


async def _table_game(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    payload = case(name)
    response = await client.put(f"/api/spades/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return payload


async def _link(client: httpx.AsyncClient, game_id: str, kind: str) -> dict[str, Any]:
    response = await client.post(f"/api/games/{game_id}/links", json={"kind": kind})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_table_mode_live_link_shows_the_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _owner(client, session)
    payload = await _table_game(client, "in progress, bids in order")
    link = await _link(client, payload["syncId"], "live")

    client.cookies.clear()
    body = (await client.get(f"/api/public/live/{link['token']}")).json()

    assert body["kind"] == "spades" and body["game"] == payload
    summary = body["summary"]
    assert summary["kind"] == "spades" and summary["hands"] == 2
    assert summary["owner"]["id"] is None and summary["live"] is True


async def test_a_table_mode_recap_shows_the_result_and_draws_its_card(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _owner(client, session)
    payload = await _table_game(client, "won on target")
    link = await _link(client, payload["syncId"], "recap")

    client.cookies.clear()
    body = (await client.get(f"/api/public/recap/{link['token']}")).json()
    image = await client.get(f"/api/public/recap/{link['token']}/og.png")
    page = (await client.get(f"/r/{link['token']}")).text

    assert body["kind"] == "spades" and body["achievements"] == []
    assert body["summary"]["result"] == {"winner": "a", "endedBy": "target"}
    assert image.status_code == 200 and image.content[:4] == b"\x89PNG"
    assert f'content="0;url=/spades?recap={link["token"]}"' in page
    assert "Riya won with 240" in page and "Spades · " in page and "3 hands" in page


async def test_a_table_mode_live_preview_opens_spades(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _owner(client, session)
    payload = await _table_game(client, "in progress, bids in order")
    link = await _link(client, payload["syncId"], "live")

    page = (await client.get(f"/l/{link['token']}")).text

    assert f'content="0;url=/spades?live={link["token"]}"' in page
    assert "Watch Riya, Arjun, Dev, Meera play Spades, live." in page


async def test_deleting_a_table_mode_game_switches_its_links_off(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _owner(client, session)
    payload = await _table_game(client, "in progress, bids in order")
    live = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")

    await client.delete(f"/api/spades/games/{payload['syncId']}")

    gone_live = await client.get(f"/api/public/live/{live['token']}")
    gone_recap = await client.get(f"/api/public/recap/{recap['token']}")
    assert (gone_live.status_code, gone_recap.status_code) == (410, 410)
    again = await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
    assert again.status_code == 404


async def test_only_the_scorer_or_an_admin_may_share(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _owner(client, session)
    payload = await _table_game(client, "won on target")
    await sign_in(client, await make_user(session, email="other@example.com", player_name="O"))

    response = await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "recap"})

    assert (response.status_code, response.json()["code"]) == (403, "forbidden")


def _held_cards(hands: list[list[dict[str, Any]]] | None) -> list[str]:
    return [json.dumps(c) for hand in hands or [] for c in hand]


async def test_an_online_live_link_never_shows_a_held_card_an_account_or_a_hidden_blind(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await _owner(client, session)
    state, hands = replay("spades-nil-blind", HIDDEN_BLIND_STEPS)
    assert state["blind"][2] and state["bids"][2] is None and hands is not None
    state = state | {"id": str(uuid.uuid4())}
    await store_table(session, owner, state)
    link = await _link(client, state["id"], "live")

    client.cookies.clear()
    response = await client.get(f"/api/public/live/{link['token']}")
    event = await load_live_event(uuid.UUID(state["id"]))

    assert response.status_code == 200, response.text
    assert event is not None
    for text in (response.text, event[1]):
        body = json.loads(text)
        assert body["kind"] == "spades-online"
        assert body["game"]["blind"] == [False, False, False, False]
        assert body["summary"]["kind"] == "spades-online" and body["summary"]["mySeat"] is None
        assert all(json.dumps(c) not in text for c in _held_cards(hands))
        assert all(account not in text for account in ACCOUNTS)
        assert {s["who"]["userId"] for s in body["game"]["seats"]} == {""}


async def test_an_online_recap_shows_the_finished_table_and_draws_its_card(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await _owner(client, session)
    state = finished_state("spades-classic")
    await store_table(session, owner, state)
    link = await _link(client, state["id"], "recap")

    client.cookies.clear()
    response = await client.get(f"/api/public/recap/{link['token']}")
    image = await client.get(f"/api/public/recap/{link['token']}/og.png")
    page = (await client.get(f"/r/{link['token']}")).text

    body = response.json()
    assert body["kind"] == "spades-online" and body["achievements"] == []
    assert body["summary"]["result"] == {"winner": "b", "endedBy": "target"}
    assert len(body["game"]["history"]) == 4
    assert all(account not in response.text for account in ACCOUNTS)
    assert image.status_code == 200 and image.content[:4] == b"\x89PNG"
    assert f'content="0;url=/spades?recap={link["token"]}"' in page
    assert "Arjun won with 209" in page
