from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Session, User
from app.domain.time import now_utc
from tests.conftest import ORIGIN
from tests.factories import (
    PASSWORD,
    make_invite,
    make_player,
    make_reset,
    make_user,
    new_player_body,
    sign_in,
)


async def test_login_sets_a_session_cookie_and_returns_user_and_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="riya@example.com", player_name="Riya")

    response = await client.post(
        "/api/auth/login", json={"email": "RIYA@example.com ", "password": PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": str(user.id),
        "email": "riya@example.com",
        "role": "member",
        "playerId": str(user.player_id),
        "createdAt": body["user"]["createdAt"],
    }
    assert body["player"]["displayName"] == "Riya"
    assert body["player"]["linkedUserId"] == str(user.id)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("kf_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


@pytest.mark.parametrize("email", ["member@example.com", "nobody@example.com"])
async def test_login_with_wrong_password_or_unknown_email_returns_bad_credentials(
    client: httpx.AsyncClient, session: AsyncSession, email: str
) -> None:
    await make_user(session)

    response = await client.post("/api/auth/login", json={"email": email, "password": "nope"})

    assert response.status_code == 401
    assert response.json()["code"] == "bad_credentials"


async def test_login_of_a_deactivated_user_returns_inactive(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, active=False)

    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": PASSWORD}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "inactive"


async def test_login_after_ten_attempts_returns_rate_limited(client: httpx.AsyncClient) -> None:
    for _ in range(10):
        await client.post("/api/auth/login", json={"email": "x@example.com", "password": "bad"})

    response = await client.post(
        "/api/auth/login", json={"email": "x@example.com", "password": "bad"}
    )

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert 1 <= response.json()["retryAfter"] <= 600


@pytest.mark.parametrize(
    "headers",
    [{"X-Kachuful": "0", "Origin": ORIGIN}, {"X-Kachuful": "1", "Origin": "https://evil.example"}],
)
async def test_write_without_app_header_or_from_another_origin_returns_csrf(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": "a@b.co", "password": "x"}, headers=headers
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf"


async def test_me_without_a_session_returns_unauthenticated(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_me_returns_the_signed_in_user(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user.id)


async def test_me_with_an_expired_session_returns_unauthenticated(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)
    await session.execute(update(Session).values(expires_at=now_utc() - timedelta(seconds=1)))

    response = await client.get("/api/auth/me")

    assert response.status_code == 401


async def test_old_session_is_extended_and_the_cookie_reissued(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)
    two_days_old = now_utc() + timedelta(days=178)
    await session.execute(
        update(Session).values(expires_at=two_days_old, last_used_at=now_utc() - timedelta(days=2))
    )

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert "kf_session=" in response.headers["set-cookie"]
    expires = (await session.execute(select(Session.expires_at))).scalar_one()
    assert expires > two_days_old + timedelta(days=1)


async def test_logout_deletes_the_session_and_clears_the_cookie(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)

    response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    assert 'kf_session=""' in response.headers["set-cookie"]
    assert (await session.execute(select(Session))).first() is None
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_get_invite_returns_email_and_preselected_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    player = await make_player(session, name="Kabir", first_name="Kabir")
    token = await make_invite(session, email="kabir@example.com", player_id=player.id)

    response = await client.get(f"/api/auth/invites/{token}")

    assert response.status_code == 200
    assert response.json()["email"] == "kabir@example.com"
    assert response.json()["player"]["displayName"] == "Kabir"
    assert response.json()["player"]["games"] == 0


@pytest.mark.parametrize("expires_in", [timedelta(seconds=-1)])
async def test_get_expired_or_unknown_invite_returns_gone(
    client: httpx.AsyncClient, session: AsyncSession, expires_in: timedelta
) -> None:
    token = await make_invite(session, expires_in=expires_in)

    expired = await client.get(f"/api/auth/invites/{token}")
    unknown = await client.get("/api/auth/invites/not-a-real-token-at-all")

    assert expired.status_code == 410
    assert expired.json()["code"] == "gone"
    assert unknown.status_code == 410


async def test_accept_invite_with_a_new_player_creates_and_signs_in_the_user(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    token = await make_invite(session, email="asha@example.com")
    body = new_player_body("Asha", firstName="Asha")

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": body}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "asha@example.com"
    assert response.json()["user"]["role"] == "member"
    assert response.json()["player"]["id"] == body["id"]
    assert response.json()["player"]["createdBy"] == response.json()["user"]["id"]
    assert "kf_session=" in response.headers["set-cookie"]
    assert (await client.get("/api/auth/me")).status_code == 200
    again = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": body}
    )
    assert again.status_code == 410


async def test_accept_invite_with_an_existing_unlinked_player_links_it(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    player = await make_player(session, name="Dev")
    token = await make_invite(session)

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": {"id": str(player.id)}}
    )

    assert response.status_code == 200
    assert response.json()["player"]["id"] == str(player.id)


async def test_accept_invite_for_a_linked_player_returns_player_linked(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="o@example.com", player_name="Owner")
    token = await make_invite(session)

    response = await client.post(
        f"/api/auth/invites/{token}",
        json={"password": "abc", "player": {"id": str(owner.player_id)}},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "player_linked"


async def test_accept_invite_with_a_taken_name_returns_name_taken_with_the_holder(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    holder = await make_player(session, name="Riya")
    token = await make_invite(session)

    response = await client.post(
        f"/api/auth/invites/{token}",
        json={"password": "abc", "player": new_player_body("  riya  ")},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "name_taken"
    assert response.json()["existing"]["id"] == str(holder.id)


async def test_accept_invite_with_an_unknown_player_returns_unknown_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    token = await make_invite(session)

    response = await client.post(
        f"/api/auth/invites/{token}",
        json={"password": "abc", "player": {"id": "00000000-0000-4000-8000-000000000000"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_player"


async def test_accept_invite_for_another_player_than_preselected_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    chosen = await make_player(session, name="Chosen")
    other = await make_player(session, name="Other")
    token = await make_invite(session, player_id=chosen.id)

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": {"id": str(other.id)}}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"


@pytest.mark.parametrize("password", ["ab", "has space", "tab\there", "x" * 129, ""])
async def test_accept_invite_with_a_weak_password_returns_weak_password(
    client: httpx.AsyncClient, session: AsyncSession, password: str
) -> None:
    token = await make_invite(session)

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": password, "player": new_player_body()}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "weak_password"


async def test_seeded_admin_accepting_the_invite_stays_admin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await make_user(
        session, email="boss@example.com", role="admin", password=None, player_name=None
    )
    token = await make_invite(session, email="boss@example.com")

    response = await client.post(
        f"/api/auth/invites/{token}", json={"password": "abc", "player": new_player_body("Jay")}
    )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(admin.id)
    assert response.json()["user"]["role"] == "admin"


async def test_reset_link_sets_a_password_and_revokes_other_sessions(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)
    token = await make_reset(session, user)

    info = await client.get(f"/api/auth/resets/{token}")
    response = await client.post(f"/api/auth/resets/{token}", json={"password": "newpass"})

    assert info.json() == {"email": user.email}
    assert response.status_code == 200
    sessions = (await session.execute(select(Session))).scalars().all()
    assert len(sessions) == 1
    reused = await client.post(f"/api/auth/resets/{token}", json={"password": "newpass"})
    assert reused.status_code == 410
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "newpass"})
    assert login.status_code == 200


async def test_reset_with_a_weak_password_or_unknown_token_fails(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    token = await make_reset(session, user)

    weak = await client.post(f"/api/auth/resets/{token}", json={"password": "a b"})
    unknown = await client.get("/api/auth/resets/unknown-token-value")

    assert weak.status_code == 422
    assert weak.json()["code"] == "weak_password"
    assert unknown.status_code == 410


async def test_change_password_keeps_this_session_and_revokes_others(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)
    other_cookie = client.cookies.get("kf_session")
    await sign_in(client, user)

    response = await client.post(
        "/api/me/password", json={"current": PASSWORD, "password": "fresh1"}
    )

    assert response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 200
    client.cookies.clear()
    assert (
        await client.get("/api/auth/me", headers={"Cookie": f"kf_session={other_cookie}"})
    ).status_code == 401


async def test_change_password_with_wrong_current_or_weak_new_fails(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    await sign_in(client, user)

    wrong = await client.post("/api/me/password", json={"current": "nope", "password": "fresh1"})
    weak = await client.post("/api/me/password", json={"current": PASSWORD, "password": "x"})

    assert wrong.status_code == 401
    assert wrong.json()["code"] == "bad_credentials"
    assert weak.status_code == 422
    assert weak.json()["code"] == "weak_password"


async def test_token_endpoints_after_thirty_calls_return_rate_limited(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(30):
        await client.get("/api/auth/invites/some-unknown-token")

    response = await client.get("/api/auth/resets/some-unknown-token")

    assert response.status_code == 429


async def test_password_hash_is_argon2id(session: AsyncSession) -> None:
    user = await make_user(session)

    stored = (await session.execute(select(User.password_hash).where(User.id == user.id))).scalar()

    assert stored is not None and stored.startswith("$argon2id$")
