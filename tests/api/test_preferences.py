import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from tests.factories import PASSWORD, make_user, sign_in

PATH = "/api/me/preferences"
SETUP = {"playerCount": 5, "decks": 1, "direction": "cw"}
EMPTY = {"theme": None, "setup": None, "updatedAt": None}


async def _member(client: httpx.AsyncClient, session: AsyncSession) -> User:
    member = await make_user(session)
    await sign_in(client, member)
    return member


async def test_a_new_account_has_no_preferences(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)

    response = await client.get("/api/auth/me")

    assert response.json()["preferences"] == EMPTY


async def test_patching_the_theme_returns_and_keeps_it(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)

    response = await client.patch(PATH, json={"theme": "editorial-paper"})

    assert response.status_code == 200
    body = response.json()
    assert body["theme"] == "editorial-paper"
    assert body["setup"] is None
    assert isinstance(body["updatedAt"], int)
    assert (await client.get("/api/auth/me")).json()["preferences"] == body


async def test_setup_and_theme_are_kept_apart(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    await client.patch(PATH, json={"setup": SETUP})

    response = await client.patch(PATH, json={"theme": "yerba-sun"})

    assert response.json()["setup"] == SETUP
    assert response.json()["theme"] == "yerba-sun"


async def test_a_null_theme_goes_back_to_the_admins_default_and_keeps_the_setup(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    await client.patch(PATH, json={"theme": "yerba-sun", "setup": SETUP})

    response = await client.patch(PATH, json={"theme": None})

    assert response.json()["theme"] is None
    assert response.json()["setup"] == SETUP


async def test_an_empty_patch_changes_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    first = await client.patch(PATH, json={"theme": "yerba-sun"})

    response = await client.patch(PATH, json={})

    assert response.json() == first.json()


async def test_preferences_come_with_the_login_answer(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    member = await _member(client, session)
    await client.patch(PATH, json={"theme": "yerba-sun"})
    await client.post("/api/auth/logout")

    response = await client.post(
        "/api/auth/login", json={"email": member.email, "password": PASSWORD}
    )

    assert response.json()["preferences"]["theme"] == "yerba-sun"


async def test_preferences_belong_to_the_signed_in_user_only(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    await client.patch(PATH, json={"theme": "yerba-sun"})
    other = await make_user(session, email="other@example.com", player_name="Other")

    await sign_in(client, other)

    assert (await client.get("/api/auth/me")).json()["preferences"] == EMPTY


async def test_preferences_need_a_signed_in_user(client: httpx.AsyncClient) -> None:
    response = await client.patch(PATH, json={"theme": "yerba-sun"})

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"theme": "neon"}, "theme"),
        ({"setup": {**SETUP, "playerCount": 8}}, "setup"),
        ({"setup": {**SETUP, "playerCount": 2}}, "setup"),
        ({"setup": {**SETUP, "decks": 3}}, "setup.decks"),
        ({"setup": {**SETUP, "direction": "up"}}, "setup.direction"),
        ({"setup": {"playerCount": 5}}, "setup.decks"),
    ],
)
async def test_an_unknown_theme_or_an_impossible_setup_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession, body: dict[str, object], field: str
) -> None:
    await _member(client, session)

    response = await client.patch(PATH, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"
    assert field in response.json()["fields"]


async def test_ten_players_are_allowed_with_two_decks(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    setup = {"playerCount": 10, "decks": 2, "direction": "ccw"}

    response = await client.patch(PATH, json={"setup": setup})

    assert response.status_code == 200
    assert response.json()["setup"] == setup
