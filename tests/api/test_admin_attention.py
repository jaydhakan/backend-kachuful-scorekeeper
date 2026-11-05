"""Admin overview `attention`: invites about to expire and players who may be one person."""

from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.time import now_utc, to_ms
from tests.factories import make_invite, make_player, make_user, sign_in
from tests.game_builder import make_payload

HOUR = timedelta(hours=1)


async def _admin(client: httpx.AsyncClient, session: AsyncSession) -> User:
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Jay")
    await sign_in(client, admin)
    return admin


async def _attention(client: httpx.AsyncClient) -> dict[str, object]:
    response = await client.get("/api/admin/overview")
    assert response.status_code == 200
    attention: dict[str, object] = response.json()["attention"]
    return attention


def _pairs(attention: dict[str, object]) -> list[tuple[str, str, str]]:
    pairs = attention["possibleDuplicates"]
    assert isinstance(pairs, list)
    return [(p["a"]["displayName"], p["b"]["displayName"], p["reason"]) for p in pairs]


async def test_invites_expiring_within_48_hours_are_counted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    await make_invite(session, email="soon@example.com", expires_in=10 * HOUR)
    await make_invite(session, email="later@example.com", expires_in=72 * HOUR)
    await make_invite(session, email="gone@example.com", expires_in=-HOUR)

    attention = await _attention(client)

    assert attention["invitesExpiringSoon"] == 1


async def test_a_player_with_someones_account_email_is_a_possible_duplicate(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    await make_user(session, email="aki@example.com", player_name="Akishesh")
    await make_player(session, name="Zed", email="AKI@example.com")

    pairs = _pairs(await _attention(client))

    assert pairs == [("Akishesh", "Zed", "email")]
    body = (await client.get("/api/admin/overview")).json()
    assert body["attention"]["possibleDuplicates"][0]["a"]["email"] is None


async def test_similar_names_are_possible_duplicates_unless_they_sat_together(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    await make_player(session, name="Riya")
    await make_player(session, name="Riyaa")
    kabir = await make_player(session, name="Kabir")
    kabirr = await make_player(session, name="Kabirr")
    third = await make_player(session, name="Neha")
    payload = make_payload(
        [str(kabir.id), str(kabirr.id), str(third.id)], start_ms=to_ms(now_utc() - 2 * HOUR)
    )
    put = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert put.status_code == 200, put.text

    pairs = _pairs(await _attention(client))

    assert ("Riya", "Riyaa", "name") in pairs or ("Riyaa", "Riya", "name") in pairs
    assert not any({a, b} == {"Kabir", "Kabirr"} for a, b, _ in pairs)
    assert not any({a, b} == {"Riya", "Neha"} for a, b, _ in pairs)


async def test_two_players_with_accounts_are_never_suggested(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    await make_user(session, email="r1@example.com", player_name="Riya")
    await make_user(session, email="r2@example.com", player_name="Riyaa")

    assert _pairs(await _attention(client)) == []


async def test_at_most_20_possible_duplicates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    for k in range(8):
        await make_player(session, name=f"Samir{k}")

    pairs = _pairs(await _attention(client))

    assert len(pairs) == 20
