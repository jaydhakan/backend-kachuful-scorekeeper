"""`GET /api/players/{id}/sikke`, its ledger and `GET /api/games/{id}/sikke`."""

import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SikkeEntry
from app.domain.time import now_utc, to_ms
from app.jobs.fake_games import MINUTE, make_payload
from app.services.merge import MergeService
from tests.factories import make_player, make_user, sign_in

HOUR = 60 * MINUTE


async def _signed_in(client: httpx.AsyncClient, session: AsyncSession) -> list[str]:
    user = await make_user(session, email="w@example.com", player_name="Wallet")
    await sign_in(client, user)
    return [str((await make_player(session, name=f"W{k}")).id) for k in range(3)]


async def _game(client: httpx.AsyncClient, ids: list[str], **kw: Any) -> dict[str, object]:
    payload = make_payload(ids, start_ms=to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=3, **kw)
    response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return payload


async def _lifetime(session: AsyncSession, player_id: str) -> int:
    stmt = select(SikkeEntry.amount).where(SikkeEntry.player_id == uuid.UUID(player_id))
    return sum((await session.execute(stmt)).scalars())


async def test_sikke_needs_a_session(client: httpx.AsyncClient) -> None:
    pid = uuid.uuid4()
    assert (await client.get(f"/api/players/{pid}/sikke")).status_code == 401
    assert (await client.get(f"/api/players/{pid}/sikke/entries")).status_code == 401
    assert (await client.get(f"/api/games/{pid}/sikke")).status_code == 401


async def test_a_player_with_no_entries_has_zeros_and_level_one(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)

    body = (await client.get(f"/api/players/{ids[0]}/sikke")).json()

    assert body == {
        "playerId": ids[0],
        "balance": 0,
        "spent": 0,
        "lifetime": 0,
        "level": {"n": 1, "from": 0, "next": 500},
    }


async def test_an_unknown_player_is_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _signed_in(client, session)
    pid = uuid.uuid4()

    wallet = await client.get(f"/api/players/{pid}/sikke")
    ledger = await client.get(f"/api/players/{pid}/sikke/entries")

    assert (wallet.status_code, wallet.json()["code"]) == (404, "not_found")
    assert (ledger.status_code, ledger.json()["code"]) == (404, "not_found")


async def test_the_wallet_sums_the_ledger(client: httpx.AsyncClient, session: AsyncSession) -> None:
    ids = await _signed_in(client, session)
    await _game(client, ids)

    body = (await client.get(f"/api/players/{ids[0]}/sikke")).json()

    lifetime = await _lifetime(session, ids[0])
    assert lifetime > 0
    assert (body["balance"], body["lifetime"]) == (lifetime, lifetime)


async def test_a_merged_away_id_answers_for_the_kept_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    await _game(client, ids)
    keep = await make_player(session, name="Keeper")
    admin = await make_user(session, email="a@example.com", role="admin", player_name=None)
    await MergeService(session).merge(uuid.UUID(ids[0]), keep.id, admin)

    body = (await client.get(f"/api/players/{ids[0]}/sikke")).json()
    ledger = (await client.get(f"/api/players/{ids[0]}/sikke/entries")).json()

    assert body["playerId"] == str(keep.id)
    assert body["lifetime"] == await _lifetime(session, str(keep.id)) > 0
    assert len(ledger["items"]) > 0


async def test_the_ledger_pages_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    first = await _game(client, ids)
    total = len(
        (await session.execute(select(SikkeEntry).where(SikkeEntry.player_id == uuid.UUID(ids[0]))))
        .scalars()
        .all()
    )

    seen: list[dict[str, object]] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await client.get(f"/api/players/{ids[0]}/sikke/entries", params=params)).json()
        seen += page["items"]
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert len(seen) == total >= 3
    times = [e["earnedAt"] for e in seen]
    assert times == sorted(times, reverse=True)
    finish = next(e for e in seen if e["kind"] == "finish")
    assert finish | {"id": None} == {
        "id": None,
        "kind": "finish",
        "amount": 30,
        "game": "kachuful",
        "gameId": first["syncId"],
        "code": None,
        "period": None,
        "earnedAt": finish["earnedAt"],
    }
    achievement = next((e for e in seen if e["kind"] == "achievement"), None)
    assert achievement is None or achievement["code"] is not None


async def test_a_bad_ledger_cursor_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)

    response = await client.get(f"/api/players/{ids[0]}/sikke/entries", params={"cursor": "%%"})

    assert (response.status_code, response.json()["code"]) == (422, "invalid")


async def test_a_game_lists_what_it_paid_each_player_in_seat_order(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    payload = await _game(client, ids)

    body = (await client.get(f"/api/games/{payload['syncId']}/sikke")).json()

    assert [item["playerId"] for item in body["items"]] == ids
    for item in body["items"]:
        assert item["amount"] == sum(e["amount"] for e in item["entries"])
        kinds = [e["kind"] for e in item["entries"]]
        assert "finish" in kinds and "streak" in kinds
        # A first game is everyone's whole lifetime: well under level 2.
        assert item["level"] == {"n": 1, "from": 0, "next": 500}
        assert item["levelUp"] is None


async def test_a_game_that_crosses_a_level_says_level_up(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    payload = await _game(client, ids)
    # Earlier credits (written after the game's recompute, so it keeps them): 480 before it.
    session.add(
        SikkeEntry(
            player_id=uuid.UUID(ids[0]),
            key="earlier",
            kind="win",
            amount=480,
            earned_at=now_utc(),
        )
    )
    await session.flush()

    body = (await client.get(f"/api/games/{payload['syncId']}/sikke")).json()

    mine = body["items"][0]
    assert mine["levelUp"] == {"from": 1, "to": 2}
    assert mine["level"] == {"n": 2, "from": 500, "next": 1500}
    assert body["items"][1]["levelUp"] is None


async def test_an_unfinished_game_pays_zero_amounts(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    payload = await _game(client, ids, finished=False)

    body = (await client.get(f"/api/games/{payload['syncId']}/sikke")).json()

    assert [i["playerId"] for i in body["items"]] == ids
    assert all(e["kind"] == "achievement" for i in body["items"] for e in i["entries"])


async def test_an_unknown_or_deleted_game_is_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _signed_in(client, session)
    payload = await _game(client, ids)
    await client.delete(f"/api/games/{payload['syncId']}")

    deleted = await client.get(f"/api/games/{payload['syncId']}/sikke")
    unknown = await client.get(f"/api/games/{uuid.uuid4()}/sikke")

    assert (deleted.status_code, deleted.json()["code"]) == (404, "not_found")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "not_found")
