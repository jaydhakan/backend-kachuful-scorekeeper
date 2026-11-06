import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import make_payload


async def _setup(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[User, User, Player, dict[str, Any]]:
    """Kabir scores a game with Guest (no account) at the table; Riya wasn't there."""
    riya = await make_user(session, email="riya@example.com", player_name="Riya")
    kabir = await make_user(session, email="kabir@example.com", player_name="Kabir")
    guest = await make_player(session, name="Guest", created_by=kabir.id)
    other = await make_player(session, name="Other")
    payload = make_payload(
        [str(kabir.player_id), str(guest.id), str(other.id)],
        start_ms=to_ms(now_utc()) - 3_600_000,
    )
    await sign_in(client, kabir)
    assert (await client.put(f"/api/games/{payload['syncId']}", json=payload)).status_code == 200
    return riya, kabir, guest, payload


async def test_a_claim_approved_by_the_scorer_merges_the_seat_into_the_claimant(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, kabir, guest, payload = await _setup(client, session)
    guest_id, riya_player = guest.id, riya.player_id
    await sign_in(client, riya)

    made = await client.post(
        f"/api/games/{payload['syncId']}/claims", json={"playerId": str(guest_id)}
    )
    again = await client.post(
        f"/api/games/{payload['syncId']}/claims", json={"playerId": str(guest_id)}
    )
    not_mine = await client.post(f"/api/claims/{made.json()['id']}/approve")

    assert made.status_code == 201
    body = made.json()
    assert body["status"] == "pending"
    assert body["player"] == {"id": str(guest_id), "displayName": "Guest"}
    assert body["claimant"]["displayName"] == "Riya"
    assert (again.status_code, again.json()["code"]) == (409, "claim_pending")
    assert not_mine.status_code == 403

    await sign_in(client, kabir)
    listed = await client.get("/api/claims")
    assert [c["id"] for c in listed.json()["items"]] == [body["id"]]
    approved = await client.post(f"/api/claims/{body['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert (await client.get("/api/claims")).json()["items"] == []
    await sign_in(client, riya)
    session.expire_all()
    merged = await session.get(Player, guest_id)
    assert merged is not None and merged.merged_into == riya_player
    mine = await client.get("/api/games", params={"involving": "true"})
    assert payload["syncId"] in {g["id"] for g in mine.json()["items"]}
    twice = await client.post(f"/api/claims/{body['id']}/reject")
    assert twice.status_code == 403


async def test_a_rejected_claim_moves_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, kabir, guest, payload = await _setup(client, session)
    guest_id = guest.id
    await sign_in(client, riya)
    claim = (
        await client.post(
            f"/api/games/{payload['syncId']}/claims", json={"playerId": str(guest_id)}
        )
    ).json()
    await sign_in(client, kabir)

    rejected = await client.post(f"/api/claims/{claim['id']}/reject")
    decided = await client.post(f"/api/claims/{claim['id']}/approve")

    assert rejected.json()["status"] == "rejected"
    assert decided.status_code == 422
    session.expire_all()
    still = await session.get(Player, guest_id)
    assert still is not None and still.merged_into is None


async def test_only_an_unlinked_player_at_that_table_can_be_claimed(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, kabir, _, payload = await _setup(client, session)
    stranger = await make_player(session, name="Stranger")
    await sign_in(client, riya)
    url = f"/api/games/{payload['syncId']}/claims"

    linked = await client.post(url, json={"playerId": str(kabir.player_id)})
    elsewhere = await client.post(url, json={"playerId": str(stranger.id)})
    no_game = await client.post(
        f"/api/games/{uuid.uuid4()}/claims", json={"playerId": str(stranger.id)}
    )
    unknown = await client.post("/api/claims/" + str(uuid.uuid4()) + "/approve")

    assert (linked.status_code, linked.json()["code"]) == (409, "player_linked")
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (422, "invalid")
    assert no_game.status_code == 404 and unknown.status_code == 404


async def test_claims_need_a_session(client: httpx.AsyncClient) -> None:
    game_id, claim_id = uuid.uuid4(), uuid.uuid4()

    responses = [
        await client.post(f"/api/games/{game_id}/claims", json={"playerId": str(uuid.uuid4())}),
        await client.get("/api/claims"),
        await client.post(f"/api/claims/{claim_id}/approve"),
        await client.post(f"/api/claims/{claim_id}/reject"),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
