"""Aliases: other names for one player, reserved like display names and shown on every read."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, PlayerAlias, User
from app.domain.names import name_key
from app.services.merge import MergeService
from tests.factories import make_player, make_user, new_player_body, sign_in


async def _member(
    client: httpx.AsyncClient, session: AsyncSession, email: str = "m@example.com", **kw: str
) -> User:
    user = await make_user(session, email=email, **kw)
    await sign_in(client, user)
    return user


async def _alias(
    client: httpx.AsyncClient, player: Player, name: str, **extra: str
) -> httpx.Response:
    return await client.post(
        f"/api/players/{player.id}/aliases", json={"displayName": name, **extra}
    )


async def test_any_member_can_add_an_alias_and_gets_the_updated_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    aki.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()

    response = await _alias(client, aki, "  Aki ", firstName="Aki", lastName="S")

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(aki.id)
    assert body["displayName"] == "Akishesh"
    assert body["aliases"] == [{"displayName": "Aki", "firstName": "Aki", "lastName": "S"}]
    await session.refresh(aki)
    assert aki.updated_at > datetime(2026, 1, 1, tzinfo=UTC)


async def test_the_same_alias_again_answers_200_without_a_second_copy(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    await _alias(client, aki, "Aki")

    again = await _alias(client, aki, "AKI")
    own_name = await _alias(client, aki, "akishesh")

    assert again.status_code == 200
    assert own_name.status_code == 200
    assert [a["displayName"] for a in own_name.json()["aliases"]] == ["Aki"]


async def test_an_alias_held_by_another_player_returns_name_taken(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    riya = await make_player(session, name="Riya")
    await _alias(client, riya, "Ri")

    as_name = await _alias(client, aki, "riya")
    as_alias = await _alias(client, aki, "RI")

    assert (as_name.status_code, as_name.json()["code"]) == (409, "name_taken")
    assert (as_alias.status_code, as_alias.json()["code"]) == (409, "name_taken")
    assert str(riya.id) not in as_alias.text


async def test_an_alias_reserves_the_name_for_new_players_and_renames(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    mine = await make_player(session, name="Zoya", created_by=user.id)
    await _alias(client, aki, "Aki")

    created = await client.post("/api/players", json=new_player_body("aki"))
    renamed = await client.patch(f"/api/players/{mine.id}", json={"displayName": "Aki"})

    assert (created.status_code, created.json()["code"]) == (409, "name_taken")
    assert (renamed.status_code, renamed.json()["code"]) == (409, "name_taken")


async def test_adding_an_alias_checks_the_name_the_player_and_the_session(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    aki = await make_player(session, name="Akishesh")
    anonymous = await _alias(client, aki, "Aki")
    admin = await _member(client, session, role="admin", email="a@example.com")
    gone = await make_player(session, name="Gone")
    await MergeService(session).merge(gone.id, aki.id, admin)

    too_long = await _alias(client, aki, "A name that is too long")
    unknown = await client.post(f"/api/players/{uuid.uuid4()}/aliases", json={"displayName": "Aki"})
    merged = await _alias(client, gone, "Gonzo")

    assert anonymous.status_code == 401
    assert too_long.status_code == 422
    assert too_long.json()["fields"] == {"displayName": "must be 1-14 characters"}
    assert unknown.status_code == 404
    assert merged.status_code == 404


async def test_aliases_show_on_the_registry_sync_and_the_player_page(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    aki.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()
    cursor = int(datetime(2026, 1, 2, tzinfo=UTC).timestamp() * 1000)
    await _alias(client, aki, "Aki")

    changed = await client.get("/api/players", params={"updatedSince": cursor})
    page = await client.get(f"/api/players/{aki.id}")

    synced = {p["id"]: p for p in changed.json()["items"]}
    assert synced[str(aki.id)]["aliases"][0]["displayName"] == "Aki"
    assert page.json()["player"]["aliases"][0]["displayName"] == "Aki"


async def test_a_player_without_aliases_has_an_empty_list(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    riya = await make_player(session, name="Riya")

    page = await client.get(f"/api/players/{riya.id}")

    assert page.json()["player"]["aliases"] == []


async def test_check_with_an_alias_and_the_email_is_a_match(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh", email="aki@example.com")
    await _alias(client, aki, "Aki")

    response = await client.get(
        "/api/players/check", params={"name": "aki", "email": "AKI@example.com"}
    )

    assert response.json()["status"] == "match"
    assert response.json()["player"]["id"] == str(aki.id)


async def test_creating_a_known_person_by_an_alias_returns_same_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh", email="aki@example.com")
    await _alias(client, aki, "Aki")

    response = await client.post(
        "/api/players", json=new_player_body("Aki", email="aki@example.com")
    )

    assert (response.status_code, response.json()["code"]) == (409, "same_player")
    assert response.json()["player"]["id"] == str(aki.id)


@pytest.mark.parametrize("held_as", ["name", "alias"])
async def test_the_database_refuses_a_name_key_another_player_holds(
    session: AsyncSession, held_as: str
) -> None:
    riya = await make_player(session, name="Riya")
    other = await make_player(session, name="Other")
    if held_as == "alias":
        session.add(PlayerAlias(player_id=riya.id, display_name="Ri", name_key=name_key("Ri")))
        await session.flush()
    taken = "Riya" if held_as == "name" else "Ri"

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                PlayerAlias(player_id=other.id, display_name=taken, name_key=name_key(taken))
            )
            await session.flush()


async def test_the_database_refuses_a_player_named_like_another_players_alias(
    session: AsyncSession,
) -> None:
    riya = await make_player(session, name="Riya")
    session.add(PlayerAlias(player_id=riya.id, display_name="Ri", name_key=name_key("Ri")))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_player(session, name="Ri")


async def test_main_alias_swaps_the_alias_and_the_display_name(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    aki = await make_player(session, name="Akishesh", created_by=user.id)
    await _alias(client, aki, "Aki")

    response = await client.patch(f"/api/players/{aki.id}", json={"mainAlias": "aki"})

    assert response.status_code == 200
    assert response.json()["displayName"] == "Aki"
    assert [a["displayName"] for a in response.json()["aliases"]] == ["Akishesh"]
    taken = await client.post("/api/players", json=new_player_body("Akishesh"))
    assert (taken.status_code, taken.json()["code"]) == (409, "name_taken")


async def test_renaming_to_an_own_alias_swaps_them(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    aki = await make_player(session, name="Akishesh", created_by=user.id)
    await _alias(client, aki, "Aki")

    response = await client.patch(f"/api/players/{aki.id}", json={"displayName": "Aki"})

    assert response.json()["displayName"] == "Aki"
    assert [a["displayName"] for a in response.json()["aliases"]] == ["Akishesh"]


async def test_main_alias_must_be_one_of_the_players_aliases(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    aki = await make_player(session, name="Akishesh", created_by=user.id)
    riya = await make_player(session, name="Riya")
    await _alias(client, riya, "Ri")
    await _alias(client, aki, "Aki")

    others = await client.patch(f"/api/players/{aki.id}", json={"mainAlias": "Ri"})
    with_name = await client.patch(
        f"/api/players/{aki.id}", json={"mainAlias": "Aki", "displayName": "Akki"}
    )
    current = await client.patch(f"/api/players/{aki.id}", json={"mainAlias": "Akishesh"})

    assert others.status_code == 422
    assert set(others.json()["fields"]) == {"mainAlias"}
    assert with_name.status_code == 422
    assert current.status_code == 200
    assert current.json()["displayName"] == "Akishesh"


async def test_only_those_who_edit_details_can_choose_the_main_alias(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    aki = await make_player(session, name="Akishesh")
    await _alias(client, aki, "Aki")

    response = await client.patch(f"/api/players/{aki.id}", json={"mainAlias": "Aki"})

    assert response.status_code == 403
