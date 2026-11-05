"""Merge: the admin picks which details the kept player keeps; every other name becomes an alias."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, PlayerAlias, PlayerPhoto, User
from app.domain.names import name_key
from app.schemas.admin import MergeKeepIn
from app.services.merge import MergeService
from tests.factories import make_player, make_user, sign_in


async def _admin(client: httpx.AsyncClient, session: AsyncSession) -> User:
    user = await make_user(session, email="a@example.com", role="admin", player_name="Adm")
    await sign_in(client, user)
    return user


async def _pair(session: AsyncSession) -> tuple[Player, Player]:
    dupe = await make_player(session, name="Aki", email="aki@example.com", first_name="Aki")
    keeper = await make_player(
        session, name="Akishesh", email="akishesh@example.com", first_name="Akishesh"
    )
    return dupe, keeper


async def test_without_keep_the_kept_player_wins_and_the_duplicates_name_becomes_an_alias(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    dupe, keeper = await _pair(session)

    response = await client.post(
        f"/api/admin/players/{dupe.id}/merge", json={"intoId": str(keeper.id)}
    )

    player = response.json()["player"]
    assert response.status_code == 200
    assert (player["displayName"], player["email"], player["firstName"]) == (
        "Akishesh",
        "akishesh@example.com",
        "Akishesh",
    )
    assert player["aliases"] == [{"displayName": "Aki", "firstName": "Aki", "lastName": None}]
    gone = await session.get(Player, dupe.id)
    assert gone is not None and gone.merged_into == keeper.id


async def test_keep_from_takes_the_duplicates_name_email_and_names(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    dupe, keeper = await _pair(session)
    keep = {"displayName": "from", "email": "from", "names": "from", "photo": "into"}

    response = await client.post(
        f"/api/admin/players/{dupe.id}/merge", json={"intoId": str(keeper.id), "keep": keep}
    )

    player = response.json()["player"]
    assert response.status_code == 200
    assert player["id"] == str(keeper.id)
    assert (player["displayName"], player["email"], player["firstName"]) == (
        "Aki",
        "aki@example.com",
        "Aki",
    )
    assert player["aliases"] == [
        {"displayName": "Akishesh", "firstName": "Akishesh", "lastName": None}
    ]


async def test_the_duplicates_aliases_move_to_the_kept_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    dupe, keeper = await _pair(session)
    session.add(PlayerAlias(player_id=dupe.id, display_name="AK", name_key=name_key("AK")))
    await session.flush()

    merged = await MergeService(session).merge(dupe.id, keeper.id, admin)

    assert [a.display_name for a in merged.player.aliases] == ["AK", "Aki"]


async def test_keep_photo_from_replaces_the_kept_players_photo(session: AsyncSession) -> None:
    admin = await make_user(session, email="a@example.com", role="admin", player_name="Adm")
    dupe, keeper = await _pair(session)
    session.add_all(
        [
            PlayerPhoto(player_id=dupe.id, image=b"dupe"),
            PlayerPhoto(player_id=keeper.id, image=b"keeper"),
        ]
    )
    await session.flush()

    await MergeService(session).merge(dupe.id, keeper.id, admin, keep=MergeKeepIn(photo="from"))

    image = (
        await session.execute(select(PlayerPhoto.image).where(PlayerPhoto.player_id == keeper.id))
    ).scalar_one()
    assert image == b"dupe"


async def test_keep_from_falls_back_to_the_kept_players_details_when_the_duplicate_has_none(
    session: AsyncSession,
) -> None:
    admin = await make_user(session, email="a@example.com", role="admin", player_name="Adm")
    dupe = await make_player(session, name="Bare")
    keeper = await make_player(session, name="Keeper", email="k@example.com", first_name="Kee")

    merged = await MergeService(session).merge(
        dupe.id, keeper.id, admin, keep=MergeKeepIn(email="from", names="from")
    )

    assert (merged.player.email, merged.player.first_name) == ("k@example.com", "Kee")


@pytest.mark.parametrize("value", ["both", "kept", ""])
async def test_an_unknown_keep_value_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession, value: str
) -> None:
    await _admin(client, session)
    dupe, keeper = await _pair(session)

    response = await client.post(
        f"/api/admin/players/{dupe.id}/merge",
        json={"intoId": str(keeper.id), "keep": {"email": value}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"


async def test_merging_with_keep_still_refuses_two_linked_players(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    member = await make_user(session, email="m@example.com", player_name="Dupe")

    response = await client.post(
        f"/api/admin/players/{member.player_id}/merge",
        json={"intoId": str(admin.player_id), "keep": {"displayName": "from"}},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "both_linked"
