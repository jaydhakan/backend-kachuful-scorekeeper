import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from tests.factories import make_player, make_user, new_player_body, sign_in


async def _member(client: httpx.AsyncClient, session: AsyncSession, **kw: str) -> User:
    user = await make_user(session, **kw)
    await sign_in(client, user)
    return user


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/players"),
        ("GET", "/api/players/check?name=x"),
        ("POST", "/api/players"),
        ("PATCH", f"/api/players/{uuid.uuid4()}"),
    ],
)
async def test_player_routes_need_a_session(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json=new_player_body())

    assert response.status_code == 401


async def test_create_player_returns_201_with_the_creator(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    body = new_player_body("  Neha   K ", firstName="Neha", lastName="Kapoor")

    response = await client.post("/api/players", json=body)

    assert response.status_code == 201
    player = response.json()
    assert player["id"] == body["id"]
    assert player["displayName"] == "Neha K"
    assert player["email"] == body["email"]
    assert player["photoUrl"] is None
    assert player["firstName"] == "Neha"
    assert player["createdBy"] == str(user.id)
    assert player["linkedUserId"] is None
    assert player["mergedInto"] is None


async def test_create_player_again_with_the_same_body_is_idempotent(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    body = new_player_body("Om")
    await client.post("/api/players", json=body)

    again = await client.post("/api/players", json=body)

    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


async def test_create_player_with_a_used_id_and_other_body_returns_id_conflict(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    body = new_player_body("Om")
    await client.post("/api/players", json=body)

    response = await client.post("/api/players", json={**body, "color": "#000000"})

    assert response.status_code == 409
    assert response.json()["code"] == "id_conflict"


async def test_create_player_with_a_taken_name_returns_name_taken_without_details(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    riya = await make_player(session, name="Riya", email="riya@example.com")

    response = await client.post("/api/players", json=new_player_body("RIYA "))

    assert response.status_code == 409
    assert response.json()["code"] == "name_taken"
    assert set(response.json()) == {"code", "message"}
    assert str(riya.id) not in response.text


@pytest.mark.parametrize("name", ["", "   ", "x" * 15])
async def test_create_player_with_a_bad_name_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession, name: str
) -> None:
    await _member(client, session)

    response = await client.post("/api/players", json=new_player_body(name))

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"


async def test_list_players_pages_in_sync_order_with_cursor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    for name in ("A1", "A2", "A3"):
        await make_player(session, name=name)

    first = await client.get("/api/players", params={"limit": 2})
    second = await client.get(
        "/api/players", params={"limit": 2, "cursor": first.json()["nextCursor"]}
    )

    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    ids = [p["id"] for p in first.json()["items"] + second.json()["items"]]
    assert len(ids) == len(set(ids)) == 4
    assert second.json()["nextCursor"] is None


async def test_list_players_since_returns_only_newer_changes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    everything = (await client.get("/api/players")).json()["items"]
    since = max(p["updatedAt"] for p in everything)
    await make_player(session, name="Late")
    await session.execute(select(1))

    response = await client.get("/api/players", params={"updatedSince": since})

    names = [p["displayName"] for p in response.json()["items"]]
    assert "Late" in names


async def test_list_players_with_a_bad_cursor_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)

    response = await client.get("/api/players", params={"cursor": "%%%"})

    assert response.status_code == 422
    assert response.json()["fields"] == {"cursor": "not a valid cursor"}


async def test_creator_can_edit_details_and_rename(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    player = await make_player(session, name="Kid", created_by=user.id)

    details = await client.patch(
        f"/api/players/{player.id}", json={"emoji": "🐼", "firstName": "Kiddo"}
    )
    rename = await client.patch(f"/api/players/{player.id}", json={"displayName": "Kidd"})

    assert details.status_code == 200
    assert details.json()["emoji"] == "🐼"
    assert details.json()["firstName"] == "Kiddo"
    assert details.json()["updatedAt"] >= details.json()["createdAt"]
    assert rename.status_code == 200
    assert rename.json()["displayName"] == "Kidd"


async def test_creator_rename_to_a_taken_name_returns_name_taken(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session)
    player = await make_player(session, name="Kid", created_by=user.id)
    await make_player(session, name="Taken")

    response = await client.patch(f"/api/players/{player.id}", json={"displayName": "taken"})

    assert response.status_code == 409
    assert response.json()["code"] == "name_taken"


async def test_stranger_cannot_rename_a_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    player = await make_player(session, name="Else")

    response = await client.patch(f"/api/players/{player.id}", json={"displayName": "Mine"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_linked_user_can_rename_their_own_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _member(client, session, player_name="Old")

    response = await client.patch(f"/api/players/{user.player_id}", json={"displayName": "New"})

    assert response.status_code == 200
    assert response.json()["displayName"] == "New"


async def test_stranger_cannot_edit_a_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    player = await make_player(session, name="Else")

    response = await client.patch(f"/api/players/{player.id}", json={"emoji": "🐸"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_admin_rename_to_a_taken_name_returns_name_taken(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session, role="admin", email="a@example.com")
    first = await make_player(session, name="First")
    await make_player(session, name="Second")

    response = await client.patch(f"/api/players/{first.id}", json={"displayName": "second"})

    assert response.status_code == 409
    assert response.json()["code"] == "name_taken"


async def test_editing_an_unknown_player_returns_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session, role="admin", email="a@example.com")

    response = await client.patch(f"/api/players/{uuid.uuid4()}", json={"emoji": "🐸"})

    assert response.status_code == 404


async def test_merge_moves_the_account_link_and_marks_the_duplicate(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session, role="admin", email="a@example.com")
    member = await make_user(session, email="m@example.com", player_name="Dupe")
    target = await make_player(session, name="Real")

    response = await client.post(
        f"/api/admin/players/{member.player_id}/merge", json={"intoId": str(target.id)}
    )

    assert response.status_code == 200
    assert response.json()["player"]["id"] == str(target.id)
    assert response.json()["player"]["linkedUserId"] == str(member.id)
    dupe = (await session.execute(select(Player).where(Player.id == member.player_id))).scalar()
    assert dupe is not None and dupe.merged_into == target.id
    assert [a["displayName"] for a in response.json()["player"]["aliases"]] == ["Dupe"]
    taken = await client.post("/api/players", json=new_player_body("Dupe"))
    assert (taken.status_code, taken.json()["code"]) == (409, "name_taken")


async def test_merge_of_two_linked_players_returns_both_linked(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _member(client, session, role="admin", email="a@example.com")
    member = await make_user(session, email="m@example.com", player_name="Dupe")

    response = await client.post(
        f"/api/admin/players/{member.player_id}/merge", json={"intoId": str(admin.player_id)}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "both_linked"


async def test_merge_into_itself_or_unknown_fails(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session, role="admin", email="a@example.com")
    player = await make_player(session, name="Solo")

    itself = await client.post(
        f"/api/admin/players/{player.id}/merge", json={"intoId": str(player.id)}
    )
    unknown = await client.post(
        f"/api/admin/players/{player.id}/merge", json={"intoId": str(uuid.uuid4())}
    )

    assert itself.status_code == 422
    assert unknown.status_code == 404


async def test_merge_by_a_member_is_forbidden(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _member(client, session)
    a = await make_player(session, name="A")
    b = await make_player(session, name="B")

    response = await client.post(f"/api/admin/players/{a.id}/merge", json={"intoId": str(b.id)})

    assert response.status_code == 403
