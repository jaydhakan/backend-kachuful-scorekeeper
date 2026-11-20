"""Nights: `close-nights` closes, pays the Sitara and posts once; later writes recompute in place."""

import argparse
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Activity, Night, SikkeEntry
from app.domain.time import now_utc, to_ms
from app.jobs import close_nights
from app.jobs.fake_games import MINUTE
from app.services import nights as nights_service
from app.services.nights import NightsService, night_uuid
from tests.factories import make_player, make_user, sign_in
from tests.placed_games import placed_payload

HOUR = 60 * MINUTE
Sent = list[tuple[object, str, dict[str, Any]]]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    """Stream events the nights service queues."""
    out: Sent = []

    async def record(_: AsyncSession, user_id: object, event: str, data: dict[str, Any]) -> None:
        out.append((user_id, event, data))

    monkeypatch.setattr(nights_service, "notify_user", record)
    return out


async def _table(client: httpx.AsyncClient, session: AsyncSession) -> tuple[list[str], uuid.UUID]:
    """Three players; the first one is the signed-in scorer's own player."""
    user = await make_user(session, email="n@example.com", player_name="Nighter")
    await sign_in(client, user)
    assert user.player_id is not None
    others = [str((await make_player(session, name=f"N{k}")).id) for k in range(2)]
    return [str(user.player_id), *others], user.id


async def _put(client: httpx.AsyncClient, payload: dict[str, Any]) -> None:
    response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text


async def _night(
    client: httpx.AsyncClient, ids: list[str], hours_ago: float
) -> list[dict[str, Any]]:
    """Two games an hour apart, the last one ended `hours_ago - 1` hours ago."""
    now = to_ms(now_utc())
    games = [
        placed_payload(ids, start_ms=now - int(hours_ago * HOUR)),
        placed_payload(ids, start_ms=now - int((hours_ago - 1) * HOUR)),
    ]
    for g in games:
        await _put(client, g)
    return games


async def _nights(session: AsyncSession) -> list[Night]:
    stmt = select(Night).order_by(Night.started_at).execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars())


async def _feed(session: AsyncSession) -> list[Activity]:
    stmt = select(Activity).where(Activity.kind == "night").order_by(Activity.id)
    stmt = stmt.execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars())


async def _sitara(session: AsyncSession) -> list[tuple[str, str, int]]:
    stmt = select(SikkeEntry).where(SikkeEntry.kind == "sitara")
    rows = (await session.execute(stmt)).scalars()
    return sorted((str(e.player_id), e.key, e.amount) for e in rows)


async def test_close_nights_stores_the_story_pays_the_sitara_and_tells_who_sat(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, user_id = await _table(client, session)
    games = await _night(client, ids, 8)

    assert await close_nights.run(argparse.Namespace(all=False)) == 0

    (night,) = await _nights(session)
    first = games[0]["syncId"]
    assert night.id == night_uuid(first)
    assert night.closed_at is not None
    assert sorted(map(str, night.games)) == sorted(g["syncId"] for g in games)
    assert sorted(map(str, night.players)) == sorted(ids)
    data = night.data
    assert data["gameIds"] == [g["syncId"] for g in games]
    assert data["gameKinds"] == ["kachuful", "kachuful"]
    assert data["from"] == games[0]["createdAt"] and data["to"] == games[1]["finishedAt"]
    assert data["winner"] == ids[0] and data["sitara"] == [ids[0]]
    assert data["gameWinners"] == [[ids[0]], [ids[0]]]
    assert set(data["sikke"]) == set(ids) and data["sikke"][ids[0]] > data["sikke"][ids[2]]
    assert {(a["playerId"], a["game"]) for a in data["achievements"]} >= {(ids[0], "kachuful")}
    assert await _sitara(session) == [(ids[0], f"sitara:{night.id}", 30)]
    (item,) = await _feed(session)
    assert item.data == {
        "nightId": str(night.id),
        "games": [g["syncId"] for g in games],
        "winner": ids[0],
    }
    assert item.player_id == uuid.UUID(ids[0])
    assert sent == [(user_id, "night", {"type": "night", "id": str(night.id)})]


async def test_closing_again_changes_nothing(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    await _night(client, ids, 8)
    await close_nights.run(argparse.Namespace(all=False))
    before = [(n.id, n.closed_at, n.data) for n in await _nights(session)]

    await close_nights.run(argparse.Namespace(all=False))

    assert [(n.id, n.closed_at, n.data) for n in await _nights(session)] == before
    assert len(await _feed(session)) == 1 and len(sent) == 1


async def test_a_night_still_going_is_not_closed(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    await _night(client, ids, 3)

    await close_nights.run(argparse.Namespace(all=False))

    assert await _nights(session) == [] and sent == []


async def test_a_game_that_joins_a_closed_night_recomputes_it_with_no_second_feed_item(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    games = await _night(client, ids, 8)
    await close_nights.run(argparse.Namespace(all=False))
    (closed,) = await _nights(session)
    # Created two hours after the night's last game ended: it links.
    late = placed_payload([ids[1], ids[0], ids[2]], start_ms=games[1]["finishedAt"] + 2 * HOUR)

    await _put(client, late)

    (night,) = await _nights(session)
    assert night.id == closed.id and night.closed_at == closed.closed_at
    assert night.data["gameIds"] == [*(g["syncId"] for g in games), late["syncId"]]
    (item,) = await _feed(session)
    assert item.data["games"] == night.data["gameIds"]
    assert len(sent) == 1


async def test_deleting_a_closed_nights_games_recomputes_then_removes_it(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    games = await _night(client, ids, 8)
    await close_nights.run(argparse.Namespace(all=False))
    (closed,) = await _nights(session)

    await client.delete(f"/api/games/{games[0]['syncId']}")

    # The earliest game went, so the night's id moves to the next one; one feed item still.
    (night,) = await _nights(session)
    assert night.id == night_uuid(games[1]["syncId"]) != closed.id
    assert night.data["gameIds"] == [games[1]["syncId"]] and night.data["sitara"] == []
    assert await _sitara(session) == []
    (item,) = await _feed(session)
    assert item.data["nightId"] == str(night.id)

    await client.delete(f"/api/games/{games[1]['syncId']}")

    assert await _nights(session) == [] and await _feed(session) == []


async def test_all_closes_past_nights_quietly(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    await _night(client, ids, 80)
    await _night(client, ids, 8)

    assert await close_nights.run(argparse.Namespace(all=True)) == 0

    assert len(await _nights(session)) == 2
    assert await _feed(session) == [] and sent == []
    assert len(await _sitara(session)) == 2


async def test_tables_without_a_shared_player_are_different_nights(
    client: httpx.AsyncClient, session: AsyncSession, sent: Sent
) -> None:
    ids, _ = await _table(client, session)
    strangers = [str((await make_player(session, name=f"X{k}")).id) for k in range(3)]
    await _night(client, ids, 8)
    await _night(client, strangers, 8)

    closed = await NightsService(session).close_due(everything=False)

    assert len(closed) == 2
    assert {frozenset(map(str, n.players)) for n in await _nights(session)} == {
        frozenset(ids),
        frozenset(strangers),
    }
