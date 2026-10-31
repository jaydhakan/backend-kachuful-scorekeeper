"""Same player = name AND email; emails only for admin, creator and linked user; invites by email."""

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.services.merge import MergeService
from tests.factories import make_invite, make_player, make_user, new_player_body, sign_in


async def _member(
    client: httpx.AsyncClient, session: AsyncSession, email: str = "m@example.com", **kw: str
) -> User:
    user = await make_user(session, email=email, **kw)
    await sign_in(client, user)
    return user


async def _check(client: httpx.AsyncClient, name: str, email: str) -> httpx.Response:
    return await client.get("/api/players/check", params={"name": name, "email": email})


async def test_check_with_name_and_email_of_one_player_is_a_match(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    riya = await make_player(session, name="Riya", email="riya@example.com")

    response = await _check(client, " riya ", "RIYA@example.com")

    assert response.json()["status"] == "match"
    assert response.json()["player"]["id"] == str(riya.id)
    assert response.json()["player"]["email"] is None


@pytest.mark.parametrize(
    ("name", "email", "status"),
    [
        ("Riya", "someone@example.com", "name_taken"),
        ("Zoya", "riya@example.com", "email_taken"),
        ("Riya", "kabir@example.com", "email_taken"),
        ("Zoya", "zoya@example.com", "new"),
    ],
)
async def test_a_partial_match_reveals_nothing(
    client: httpx.AsyncClient, session: AsyncSession, name: str, email: str, status: str
) -> None:
    await _member(client, session)
    await make_player(session, name="Riya", email="riya@example.com")
    await make_player(session, name="Kabir", email="kabir@example.com")

    response = await _check(client, name, email)

    assert response.status_code == 200
    assert response.json() == {"status": status, "player": None}


async def test_check_needs_a_valid_email_and_a_session(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    anonymous = await _check(client, "Riya", "riya@example.com")
    await _member(client, session)

    missing = await client.get("/api/players/check", params={"name": "Riya"})
    malformed = await _check(client, "Riya", "not an email")

    assert anonymous.status_code == 401
    assert missing.status_code == 422
    assert malformed.json()["fields"] == {"email": "must look like x@y.z"}


async def test_check_is_limited_to_60_a_minute(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    for _ in range(60):
        await _check(client, "A", "a@example.com")

    response = await _check(client, "A", "a@example.com")

    assert response.status_code == 429


async def test_creating_a_known_person_returns_same_player_with_that_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    riya = await make_player(session, name="Riya", email="riya@example.com")

    response = await client.post(
        "/api/players", json=new_player_body("riya", email="Riya@Example.com")
    )

    assert response.status_code == 409
    assert response.json()["code"] == "same_player"
    assert response.json()["player"]["id"] == str(riya.id)
    assert response.json()["player"]["email"] is None


async def test_creating_with_someone_elses_email_returns_email_taken_without_details(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    riya = await make_player(session, name="Riya", email="riya@example.com")

    response = await client.post(
        "/api/players", json=new_player_body("Zoya", email="riya@example.com")
    )

    assert response.status_code == 409
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == "email_taken"
    assert str(riya.id) not in response.text


@pytest.mark.parametrize("email", [None, "", "no-at-sign", "a b@c.d"])
async def test_creating_without_a_valid_email_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession, email: str | None
) -> None:
    await _member(client, session)
    body: dict[str, object] = {**new_player_body("Zoya"), "email": email}

    response = await client.post("/api/players", json=body)

    assert response.status_code == 422
    assert response.json()["fields"] == {"email": "must look like x@y.z"}


async def test_emails_are_stored_trimmed_and_lower_cased(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)

    response = await client.post(
        "/api/players", json=new_player_body("Zoya", email="  Zoya@Example.COM ")
    )

    assert response.json()["email"] == "zoya@example.com"


async def test_only_admin_creator_and_linked_user_see_a_players_email(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    creator = await _member(client, session, email="creator@example.com", player_name="Creator")
    created = await client.post("/api/players", json=new_player_body("Zoya", email="z@example.com"))
    zoya_id = created.json()["id"]
    linked = await make_user(session, email="z-user@example.com", player_name=None)
    linked.player_id = uuid.UUID(zoya_id)
    await session.flush()

    seen = {}
    for viewer, role in ((creator, "creator"), (linked, "linked")):
        client.cookies.clear()
        await sign_in(client, viewer)
        items = (await client.get("/api/players", params={"limit": 200})).json()["items"]
        seen[role] = next(p for p in items if p["id"] == zoya_id)["email"]
    client.cookies.clear()
    await _member(client, session, email="stranger@example.com", player_name="Stranger")
    items = (await client.get("/api/players", params={"limit": 200})).json()["items"]
    seen["stranger"] = next(p for p in items if p["id"] == zoya_id)["email"]
    client.cookies.clear()
    await _member(client, session, email="boss@example.com", role="admin", player_name="Boss")
    items = (await client.get("/api/players", params={"limit": 200})).json()["items"]
    seen["admin"] = next(p for p in items if p["id"] == zoya_id)["email"]
    page = await client.get(f"/api/players/{zoya_id}")

    assert seen == {
        "creator": "z@example.com",
        "linked": "z@example.com",
        "stranger": None,
        "admin": "z@example.com",
    }
    assert page.json()["player"]["email"] is None


async def test_patching_an_email_checks_permission_and_uniqueness(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    mine = await make_player(session, name="Mine", created_by=user.id)
    other = await make_player(session, name="Other", email="other@example.com")

    ok = await client.patch(f"/api/players/{mine.id}", json={"email": "Mine@Example.com"})
    taken = await client.patch(f"/api/players/{mine.id}", json={"email": "other@example.com"})
    bad = await client.patch(f"/api/players/{mine.id}", json={"email": "nope"})
    forbidden = await client.patch(f"/api/players/{other.id}", json={"email": "x@example.com"})

    assert ok.json()["email"] == "mine@example.com"
    assert taken.status_code == 409 and taken.json()["code"] == "email_taken"
    assert bad.status_code == 422
    assert forbidden.status_code == 403


async def test_invite_shows_the_unlinked_player_with_the_invite_email(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya = await make_player(session, name="Riya", email="riya@example.com")
    token = await make_invite(session, email="riya@example.com")

    response = await client.get(f"/api/auth/invites/{token}")

    assert response.json()["player"]["id"] == str(riya.id)
    assert response.json()["player"]["email"] is None


async def test_a_new_player_from_an_invite_gets_the_invite_email(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    token = await make_invite(session, email="asha@example.com")

    response = await client.post(
        f"/api/auth/invites/{token}",
        json={"password": "abc", "player": new_player_body("Asha", email="other@example.com")},
    )

    assert response.status_code == 200
    assert response.json()["player"]["email"] == "asha@example.com"


async def test_claiming_a_player_without_email_gives_it_the_invite_email(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    old = await make_player(session, name="Old")
    token = await make_invite(session, email="old@example.com")

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": {"id": str(old.id)}}
    )

    assert response.status_code == 200
    assert response.json()["player"]["email"] == "old@example.com"


async def test_claiming_someone_elses_player_returns_email_taken(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    other = await make_player(session, name="Other", email="other@example.com")
    token = await make_invite(session, email="me@example.com")

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": {"id": str(other.id)}}
    )

    assert response.status_code == 409
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == "email_taken"


async def test_invite_errors_never_name_the_other_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya = await make_player(session, name="Riya", email="riya@example.com")
    token = await make_invite(session, email="riya@example.com")

    full_match = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": new_player_body("Riya")}
    )

    assert full_match.status_code == 409
    assert full_match.json()["code"] == "email_taken"
    assert str(riya.id) not in full_match.text


async def test_merge_keeps_the_targets_email_or_takes_the_duplicates(
    session: AsyncSession,
) -> None:
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Adm")
    dupe = await make_player(session, name="Dupe", email="dupe@example.com")
    bare = await make_player(session, name="Bare")
    keeper = await make_player(session, name="Keeper", email="keeper@example.com")
    other = await make_player(session, name="Other", email="other@example.com")

    await MergeService(session).merge(dupe.id, bare.id, admin)
    await MergeService(session).merge(other.id, keeper.id, admin)

    emails = dict((await session.execute(select(Player.display_name, Player.email))).all())
    assert emails["Bare"] == "dupe@example.com"
    assert emails["Keeper"] == "keeper@example.com"
