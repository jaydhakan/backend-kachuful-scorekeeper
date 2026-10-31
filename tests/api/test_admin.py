import argparse
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, Session, User
from app.jobs import seed_admin
from app.settings import settings
from tests.factories import PASSWORD, make_invite, make_player, make_user, sign_in

ADMIN_ROUTES = [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/invites", {"email": "x@example.com"}),
    ("DELETE", "/api/admin/invites/x@example.com", None),
    ("POST", f"/api/admin/users/{uuid.uuid4()}/reset-link", None),
    ("PATCH", f"/api/admin/users/{uuid.uuid4()}", {"active": False}),
]


async def _admin(client: httpx.AsyncClient, session: AsyncSession) -> User:
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Jay")
    await sign_in(client, admin)
    return admin


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_admin_routes_refuse_members_and_strangers(
    client: httpx.AsyncClient,
    session: AsyncSession,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    anonymous = await client.request(method, path, json=body)
    member = await make_user(session)
    await sign_in(client, member)
    as_member = await client.request(method, path, json=body)

    assert anonymous.status_code == 401
    assert as_member.status_code == 403
    assert as_member.json()["code"] == "forbidden"


async def test_create_invite_returns_a_copyable_link_valid_for_seven_days(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)

    response = await client.post("/api/admin/invites", json={"email": " New@Example.com "})

    assert response.status_code == 201
    url = response.json()["inviteUrl"]
    assert url.startswith(f"{settings.APP_ORIGIN}/?invite=")
    token = url.split("=", 1)[1]
    info = await client.get(f"/api/auth/invites/{token}")
    assert info.json()["email"] == "new@example.com"
    invite = (await session.execute(select(Invite))).scalar_one()
    assert response.json()["expiresAt"] == int(invite.expires_at.timestamp() * 1000)


async def test_a_new_invite_replaces_the_pending_one(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    first = await client.post("/api/admin/invites", json={"email": "n@example.com"})

    await client.post("/api/admin/invites", json={"email": "n@example.com"})

    old_token = first.json()["inviteUrl"].split("=", 1)[1]
    assert (await client.get(f"/api/auth/invites/{old_token}")).status_code == 410


async def test_inviting_an_existing_user_returns_user_exists(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    member = await make_user(session)

    response = await client.post("/api/admin/invites", json={"email": member.email})

    assert response.status_code == 409
    assert response.json()["code"] == "user_exists"


async def test_inviting_with_an_unknown_or_linked_player_fails(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)

    unknown = await client.post(
        "/api/admin/invites", json={"email": "a@example.com", "playerId": str(uuid.uuid4())}
    )
    linked = await client.post(
        "/api/admin/invites", json={"email": "a@example.com", "playerId": str(admin.player_id)}
    )

    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_player"
    assert linked.status_code == 409
    assert linked.json()["code"] == "player_linked"


async def test_inviting_a_bad_email_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)

    response = await client.post("/api/admin/invites", json={"email": "not-an-email"})

    assert response.status_code == 422
    assert response.json()["fields"] == {"email": "invalid"}


async def test_revoking_an_invite_makes_its_link_gone_and_is_idempotent(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    token = await make_invite(session, email="bye@example.com")

    first = await client.delete("/api/admin/invites/bye@example.com")
    second = await client.delete("/api/admin/invites/bye@example.com")

    assert first.status_code == 204
    assert second.status_code == 204
    assert (await client.get(f"/api/auth/invites/{token}")).status_code == 410


async def test_list_users_shows_accounts_and_invite_only_rows(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    pre = await make_player(session, name="Pending")
    await make_invite(session, email="pending@example.com", player_id=pre.id)

    response = await client.get("/api/admin/users")

    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["user"]["id"] == str(admin.id)
    assert rows[0]["active"] is True
    assert rows[0]["lastSeenAt"] is not None
    assert rows[0]["invite"] is None
    assert rows[1]["user"] is None
    assert rows[1]["player"]["displayName"] == "Pending"
    assert rows[1]["invite"]["email"] == "pending@example.com"
    assert set(rows[1]["invite"]) == {"email", "expiresAt"}


async def test_reset_link_is_one_time_and_unknown_user_is_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    member = await make_user(session)

    response = await client.post(f"/api/admin/users/{member.id}/reset-link")
    missing = await client.post(f"/api/admin/users/{uuid.uuid4()}/reset-link")

    assert response.status_code == 201
    assert response.json()["resetUrl"].startswith(f"{settings.APP_ORIGIN}/?reset=")
    assert missing.status_code == 404


async def test_deactivating_a_user_revokes_their_sessions(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    member = await make_user(session)
    await sign_in(client, member)
    client.cookies.clear()
    await _admin(client, session)

    response = await client.patch(f"/api/admin/users/{member.id}", json={"active": False})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(member.id)
    owned = await session.execute(select(Session).where(Session.user_id == member.id))
    assert owned.first() is None
    client.cookies.clear()
    login = await client.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})
    assert login.json()["code"] == "inactive"


async def test_the_last_active_admin_cannot_be_deactivated(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)

    response = await client.patch(f"/api/admin/users/{admin.id}", json={"active": False})

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"


async def test_seed_admin_creates_the_admin_and_prints_an_invite(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await seed_admin.run(argparse.Namespace())

    out = capsys.readouterr().out
    assert code == 0
    assert f"{settings.APP_ORIGIN}/?invite=" in out
    admin = (
        await session.execute(select(User).where(User.email == settings.SEED_ADMIN_EMAIL))
    ).scalar_one()
    assert admin.role == "admin"
    assert admin.password_hash is None


async def test_seed_admin_twice_issues_a_fresh_link_and_revokes_the_old(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_admin.run(argparse.Namespace())
    await seed_admin.run(argparse.Namespace())

    pending = (await session.execute(select(Invite).where(Invite.revoked_at.is_(None)))).scalars()
    assert len(list(pending)) == 1
    assert capsys.readouterr().out.count("?invite=") == 2
