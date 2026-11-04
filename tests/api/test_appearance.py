import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSettingAudit, User
from tests.factories import make_user, sign_in

PATH = "/api/settings/appearance"
ADMIN_PATH = "/api/admin/settings/appearance"
DEFAULTS = {
    "theme": "sunset-glass",
    "allowUserOverride": False,
    "updatedAt": None,
    "updatedBy": None,
}


async def _admin(
    client: httpx.AsyncClient, session: AsyncSession, player_name: str | None = "Jay"
) -> User:
    admin = await make_user(
        session, email="admin@example.com", role="admin", player_name=player_name
    )
    await sign_in(client, admin)
    return admin


async def test_appearance_before_any_change_is_the_default_for_strangers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(PATH)

    assert response.status_code == 200
    assert response.json() == DEFAULTS
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.headers["etag"]


async def test_appearance_with_its_etag_returns_304(client: httpx.AsyncClient) -> None:
    etag = (await client.get(PATH)).headers["etag"]

    response = await client.get(PATH, headers={"If-None-Match": f"W/{etag}"})

    assert response.status_code == 304
    assert response.headers["etag"] == etag


async def test_appearance_is_one_query(client: httpx.AsyncClient, queries: list[str]) -> None:
    await client.get(PATH)

    assert len([q for q in queries if "SAVEPOINT" not in q]) == 1


async def test_admin_sets_the_appearance_for_everyone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    etag = (await client.get(PATH)).headers["etag"]

    response = await client.put(ADMIN_PATH, json={"theme": "yerba-sun", "allowUserOverride": True})

    assert response.status_code == 200
    body = response.json()
    assert body["theme"] == "yerba-sun"
    assert body["allowUserOverride"] is True
    assert body["updatedBy"] == "Jay"
    assert isinstance(body["updatedAt"], int)
    client.cookies.clear()
    seen = await client.get(PATH, headers={"If-None-Match": etag})
    assert seen.status_code == 200
    assert seen.json() == body


async def test_admin_sets_emerald_gilt_and_strangers_see_it(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)

    response = await client.put(
        ADMIN_PATH, json={"theme": "emerald-gilt", "allowUserOverride": False}
    )

    assert response.status_code == 200
    assert response.json()["theme"] == "emerald-gilt"
    client.cookies.clear()
    assert (await client.get(PATH)).json()["theme"] == "emerald-gilt"


async def test_each_changed_setting_is_audited_with_old_and_new_value(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)

    await client.put(ADMIN_PATH, json={"theme": "editorial-paper", "allowUserOverride": False})

    rows = (await session.execute(select(AppSettingAudit))).scalars().all()
    assert [(r.key, r.old_value, r.new_value, r.changed_by_user_id) for r in rows] == [
        ("theme.default", "sunset-glass", "editorial-paper", admin.id)
    ]


async def test_setting_the_same_appearance_again_changes_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    body = {"theme": "yerba-sun", "allowUserOverride": True}
    first = await client.put(ADMIN_PATH, json=body)

    again = await client.put(ADMIN_PATH, json=body)

    assert again.json() == first.json()
    audit = (await session.execute(select(AppSettingAudit))).scalars().all()
    assert len(audit) == 2


async def test_updated_by_is_null_for_an_admin_without_a_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session, player_name=None)

    response = await client.put(ADMIN_PATH, json={"theme": "yerba-sun", "allowUserOverride": False})

    assert response.json()["updatedBy"] is None


async def test_public_appearance_never_shows_the_admins_email_or_id(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    await client.put(ADMIN_PATH, json={"theme": "yerba-sun", "allowUserOverride": True})
    client.cookies.clear()

    text = (await client.get(PATH)).text

    assert admin.email not in text
    assert str(admin.id) not in text


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"theme": "neon", "allowUserOverride": False}, "theme"),
        ({"theme": "yerba-sun", "allowUserOverride": "yes"}, "allowUserOverride"),
        ({"theme": "yerba-sun", "allowUserOverride": 1}, "allowUserOverride"),
        ({"theme": "yerba-sun"}, "allowUserOverride"),
    ],
)
async def test_an_unknown_theme_or_a_non_boolean_option_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession, body: dict[str, object], field: str
) -> None:
    await _admin(client, session)

    response = await client.put(ADMIN_PATH, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"
    assert field in response.json()["fields"]
