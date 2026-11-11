"""Players are shared by every game: Kaali Teeri seats count in deletes, counters, merges and jobs."""

import argparse
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, KtGamePlayer, KtHand, Player, User
from app.jobs import prune_empty
from tests.factories import make_player, make_user, sign_in
from tests.kt_builder import Registry, fixture_payload, freeze_clock, seat_registry

BASE = "/api/kaali-teeri/games"


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)


async def _upload_as(
    client: httpx.AsyncClient, session: AsyncSession, user: User, registry: Registry
) -> dict[str, Any]:
    await sign_in(client, user)
    payload, _ = await fixture_payload(session, "kt-4p", registry)
    response = await client.put(f"{BASE}/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return payload


async def _rows(session: AsyncSession, model: Any, game_id: str) -> int:
    stmt = select(func.count()).select_from(model).where(model.game_id == uuid.UUID(game_id))
    return int((await session.execute(stmt)).scalar_one())


async def test_a_player_who_only_played_kaali_teeri_survives_their_creators_data_delete(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    creator = await make_user(session, email="creator@example.com", player_name="Creator")
    scorer = await make_user(session, email="scorer@example.com", player_name="Scorer")
    kept = await make_player(session, name="Kt-ana", created_by=creator.id)
    unused = await make_player(session, name="Nobody", created_by=creator.id)
    registry = await seat_registry(session, {"ben", "cy", "dee"}, {"ana": kept})
    await _upload_as(client, session, scorer, registry)

    await sign_in(client, creator)
    response = await client.delete("/api/me/data")

    assert response.json() == {"games": 0, "players": 1}
    assert await session.get(Player, kept.id, populate_existing=True) is not None
    assert await session.get(Player, unused.id, populate_existing=True) is None


async def test_start_fresh_deletes_the_callers_kaali_teeri_games(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    scorer = await make_user(session, email="scorer@example.com", player_name="Scorer")
    payload = await _upload_as(client, session, scorer, {})

    response = await client.delete("/api/me/data")

    assert response.status_code == 200 and response.json()["games"] == 1
    row = await session.get(Game, uuid.UUID(payload["syncId"]), populate_existing=True)
    assert row is not None and row.deleted_at is not None
    assert await _rows(session, KtHand, payload["syncId"]) == 0


async def test_admin_preview_clear_and_delete_user_cover_kaali_teeri(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    member = await make_user(session, email="member@example.com", player_name="Member")
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Ad")
    registry: Registry = {}
    first = await _upload_as(client, session, member, registry)

    await sign_in(client, admin)
    preview = (await client.get(f"/api/admin/users/{member.id}/deletion-preview")).json()
    cleared = (await client.delete(f"/api/admin/users/{member.id}/games")).json()
    await sign_in(client, member)
    second, _ = await fixture_payload(session, "kt-5p", registry)
    assert (await client.put(f"{BASE}/{second['syncId']}", json=second)).status_code == 200
    await sign_in(client, admin)
    deleted = await client.delete(f"/api/admin/users/{member.id}")

    assert (preview["games"], preview["rounds"], preview["otherPlayers"]) == (1, 4, 4)
    assert cleared == {"games": 1}
    assert deleted.status_code == 204
    for payload in (first, second):
        row = await session.get(Game, uuid.UUID(payload["syncId"]), populate_existing=True)
        assert row is not None and row.deleted_at is not None


async def test_playing_kaali_teeri_counts_on_the_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    scorer = await make_user(session, email="scorer@example.com", player_name="Scorer")
    payload = await _upload_as(client, session, scorer, {})
    ana_id = next(s["playerId"] for s in payload["players"])

    listed = (await client.get("/api/players?limit=200")).json()["items"]
    ana = next(p for p in listed if p["id"] == ana_id)
    page = (await client.get(f"/api/players/{ana_id}")).json()

    assert page["player"]["games"] == 1 and page["player"]["lastPlayedAt"] is not None
    assert ana["id"] == ana_id


async def test_merging_a_kaali_teeri_only_player_moves_their_seat(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Ad")
    payload = await _upload_as(client, session, admin, {})
    source = uuid.UUID(payload["players"][0]["playerId"])
    target = await make_player(session, name="Ana Proper")

    response = await client.post(
        f"/api/admin/players/{source}/merge", json={"intoId": str(target.id)}
    )

    assert response.status_code == 200, response.text
    row = await session.get(Game, uuid.UUID(payload["syncId"]), populate_existing=True)
    assert row is not None and row.payload["players"][0]["playerId"] == str(target.id)
    seats = await session.execute(
        select(KtGamePlayer.player_id).where(KtGamePlayer.game_id == row.id)
    )
    assert target.id in set(seats.scalars()) and source not in set(seats.scalars())
    bids = await session.execute(select(KtHand.bidder_id).where(KtHand.game_id == row.id))
    assert source not in set(bids.scalars())
    stats = (await client.get(f"/api/kaali-teeri/players/{target.id}/stats")).json()
    assert stats["games"] == 1
    await session.refresh(target)
    assert target.games_count == 1


async def test_prune_empty_deletes_a_stored_kaali_teeri_game_with_no_hand_done(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    scorer = await make_user(session, email="scorer@example.com", player_name="Scorer")
    payload = await _upload_as(client, session, scorer, {})
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None
    row.round_count = 0  # as a game stored before the no-trace rule would be
    await session.flush()

    await prune_empty.run(argparse.Namespace(dry_run=False))

    await session.refresh(row)
    assert row.deleted_at is not None
    assert await _rows(session, KtGamePlayer, payload["syncId"]) == 0
