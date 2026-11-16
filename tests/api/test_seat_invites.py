"""Seat invites: Ishaara's create invites, the routes' happy paths, errors and permissions, the
lifecycle with expiry worked out on read, and the generic create every kind's route calls."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SeatInvite, User
from app.exceptions import ApiError
from app.security import rate_limit
from app.services import seat_invites
from app.services.seat_invites import SeatInvitesService
from tests.factories import make_player, make_user
from tests.ishaara_builder import (
    ClientFor,
    Clock,
    Table,
    client_for_fixture,  # noqa: F401 - fixture
    create,
    make_table,
)


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    frozen = Clock()
    monkeypatch.setattr(seat_invites, "now_utc", frozen.now)
    return frozen


async def _mine(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/api/seat-invites")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def _rows(session: AsyncSession, game_id: str) -> list[SeatInvite]:
    stmt = select(SeatInvite).where(SeatInvite.game_id == uuid.UUID(game_id))
    return list((await session.execute(stmt.execution_options(populate_existing=True))).scalars())


async def test_ishaara_create_invites_each_clue_giver_but_not_the_host(
    table: Table, session: AsyncSession, clock: Clock
) -> None:
    game = (await create(table))["game"]

    rows = await _rows(session, game["id"])
    assert {(r.seat, r.to_user, r.status) for r in rows} == {
        ("clue:sun", table.sun.id, "pending"),
        ("clue:moon", table.moon.id, "pending"),
    }
    (item,) = await _mine(table.clients["sun"])
    assert item == {
        "id": str(next(r.id for r in rows if r.seat == "clue:sun")),
        "kind": "ishaara",
        "gameId": game["id"],
        "seat": "clue:sun",
        "status": "pending",
        "createdAt": clock.ms,
        "from": {
            "userId": str(table.host.id),
            "name": "Host",
            "emoji": "🦊",
            "color": "#ff8a65",
            "photoUrl": None,
        },
        "table": {"name": "Host's game", "seatsTaken": []},
        "partner": None,
    }
    assert await _mine(table.clients["host"]) == []


async def test_a_host_who_gives_clues_is_not_invited(
    table: Table, session: AsyncSession, client_for: ClientFor
) -> None:
    body = table.body()
    body["players"][0]["playerId"] = str(table.host.player_id)

    response = await table.clients["host"].post("/api/ishaara/games", json=body)

    assert response.status_code == 201, response.text
    rows = await _rows(session, body["id"])
    assert [(r.seat, r.to_user) for r in rows] == [("clue:moon", table.moon.id)]


async def test_the_list_carries_the_server_clock(table: Table, clock: Clock) -> None:
    response = await table.clients["sun"].get("/api/seat-invites")

    assert response.status_code == 200
    assert response.json() == {"items": [], "serverNow": clock.ms}


async def test_signed_out_reads_are_refused(client: httpx.AsyncClient) -> None:
    for path in ("/api/seat-invites", "/api/me/circle"):
        response = await client.get(path)
        assert (response.status_code, response.json()["code"]) == (401, "unauthenticated")


async def test_accept_marks_it_accepted_and_is_idempotent(
    table: Table, session: AsyncSession, clock: Clock
) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])

    first = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")
    again = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted" and again.json()["status"] == "accepted"
    assert await _mine(table.clients["sun"]) == []
    row = next(r for r in await _rows(session, game["id"]) if r.seat == "clue:sun")
    assert row.status == "accepted" and row.responded_at is not None
    state = (await table.clients["host"].get(f"/api/ishaara/games/{game['id']}")).json()["game"]
    assert state["joined"] == {"sun": False, "moon": False}


async def test_opening_the_key_accepts_the_invite(table: Table, session: AsyncSession) -> None:
    game = (await create(table))["game"]

    await table.clients["moon"].get(f"/api/ishaara/games/{game['id']}/key")

    statuses = {r.seat: r.status for r in await _rows(session, game["id"])}
    assert statuses == {"clue:sun": "pending", "clue:moon": "accepted"}


async def test_only_the_invitee_can_accept_or_decline(table: Table) -> None:
    await create(table)
    (item,) = await _mine(table.clients["sun"])

    for who in ("host", "moon"):
        for action in ("accept", "decline"):
            response = await table.clients[who].post(f"/api/seat-invites/{item['id']}/{action}")
            assert (response.status_code, response.json()["code"]) == (404, "not_found")
    missing = await table.clients["sun"].post(f"/api/seat-invites/{uuid.uuid4()}/accept")
    assert missing.status_code == 404


async def test_decline_then_accept_is_expired(table: Table, session: AsyncSession) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])

    declined = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/decline")
    again = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/decline")
    late = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")

    assert (declined.status_code, again.status_code) == (204, 204)
    assert (late.status_code, late.json()["code"]) == (410, "expired")
    row = next(r for r in await _rows(session, game["id"]) if r.seat == "clue:sun")
    assert row.status == "declined"
    state = (await table.clients["host"].get(f"/api/ishaara/games/{game['id']}")).json()["game"]
    assert state["status"] == "waiting"


async def test_the_host_cancels_and_the_invitee_cannot(table: Table, session: AsyncSession) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])

    by_invitee = await table.clients["sun"].delete(f"/api/seat-invites/{item['id']}")
    by_other = await table.clients["moon"].delete(f"/api/seat-invites/{item['id']}")
    cancelled = await table.clients["host"].delete(f"/api/seat-invites/{item['id']}")
    again = await table.clients["host"].delete(f"/api/seat-invites/{item['id']}")
    late = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")

    assert (by_invitee.status_code, by_invitee.json()["code"]) == (403, "not_host")
    assert (by_other.status_code, by_other.json()["code"]) == (404, "not_found")
    assert (cancelled.status_code, again.status_code) == (204, 204)
    assert await _mine(table.clients["sun"]) == []
    assert (late.status_code, late.json()["code"]) == (410, "expired")
    row = next(r for r in await _rows(session, game["id"]) if r.seat == "clue:sun")
    assert row.status == "cancelled"


async def test_after_30_minutes_it_reads_as_gone_and_accept_is_expired(
    table: Table, session: AsyncSession, clock: Clock
) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])
    clock.advance(30 * 60)
    assert len(await _mine(table.clients["sun"])) == 1
    clock.advance(1)

    listed = await _mine(table.clients["sun"])
    late = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")

    assert listed == []
    assert (late.status_code, late.json()["code"]) == (410, "expired")
    row = next(r for r in await _rows(session, game["id"]) if r.seat == "clue:sun")
    assert row.status == "expired"


async def test_declining_an_out_of_date_invite_records_it_expired(
    table: Table, session: AsyncSession, clock: Clock
) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])
    clock.advance(31 * 60)

    response = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/decline")

    assert response.status_code == 204
    row = next(r for r in await _rows(session, game["id"]) if r.seat == "clue:sun")
    assert row.status == "expired"


async def test_an_ended_game_expires_its_invites(table: Table, session: AsyncSession) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])

    deleted = await table.clients["host"].delete(f"/api/ishaara/games/{game['id']}")
    late = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")

    assert deleted.status_code == 204
    assert await _mine(table.clients["sun"]) == []
    assert (late.status_code, late.json()["code"]) == (410, "expired")
    assert {r.status for r in await _rows(session, game["id"])} == {"expired"}


async def test_a_finished_game_reads_as_expired_even_before_it_is_stored(
    table: Table, session: AsyncSession
) -> None:
    game = (await create(table))["game"]
    (item,) = await _mine(table.clients["sun"])
    # Only the game row says it is over; the invite row is still pending.
    stored = await session.get(Game, uuid.UUID(game["id"]))
    assert stored is not None
    stored.status = "finished"
    await session.flush()

    assert await _mine(table.clients["sun"]) == []
    late = await table.clients["sun"].post(f"/api/seat-invites/{item['id']}/accept")
    assert (late.status_code, late.json()["code"]) == (410, "expired")


# The generic create (each kind's own `POST .../seat-invites` route calls it), through Ishaara.
# The host sits at the table, so both clue-givers are in their circle.


async def _seated_game(table: Table) -> dict[str, Any]:
    game: dict[str, Any] = (await create(table, players=table.seats(host_seated=True)))["game"]
    return game


async def _invite(session: AsyncSession, game_id: str, host: User, to: User, seat: str) -> Any:
    return await SeatInvitesService(session).create(
        kind="ishaara", game_id=uuid.UUID(game_id), host=host, to_user_id=to.id, seat=seat
    )


async def test_a_re_invite_expires_the_earlier_one(
    table: Table, session: AsyncSession, clock: Clock
) -> None:
    game = await _seated_game(table)
    (first,) = await _mine(table.clients["sun"])
    clock.advance(31 * 60)
    assert await _mine(table.clients["sun"]) == []

    again = await _invite(session, game["id"], table.host, table.sun, "clue:sun")

    (listed,) = await _mine(table.clients["sun"])
    assert listed["id"] == str(again.id) != first["id"] and listed["status"] == "pending"
    statuses = {str(r.id): r.status for r in await _rows(session, game["id"])}
    assert statuses[first["id"]] == "expired"
    late = await table.clients["sun"].post(f"/api/seat-invites/{first['id']}/accept")
    assert (late.status_code, late.json()["code"]) == (410, "expired")


async def _code(session: AsyncSession, game_id: str, host: User, to: User, seat: str) -> Any:
    with pytest.raises(ApiError) as error:
        await _invite(session, game_id, host, to, seat)
    return error.value.status, error.value.code.value


async def test_create_errors_and_permissions(table: Table, session: AsyncSession) -> None:
    game = await _seated_game(table)
    outsider = await make_user(session, email="out@example.com", player_name="Outsider")
    no_player = await make_user(session, email="np@example.com", player_name=None)
    gone = await make_user(session, email="gone@example.com", player_name="Gone", active=False)
    gid = game["id"]

    assert await _code(session, str(uuid.uuid4()), table.host, table.sun, "clue:sun") == (
        404,
        "not_found",
    )
    assert await _code(session, gid, table.sun, table.moon, "clue:moon") == (403, "not_host")
    assert await _code(session, gid, table.host, no_player, "clue:sun") == (409, "no_account")
    assert await _code(session, gid, table.host, gone, "clue:sun") == (409, "no_account")
    assert await _code(session, gid, table.host, outsider, "clue:sun") == (409, "not_in_circle")
    assert await _code(session, gid, table.host, table.host, "clue:sun") == (409, "not_in_circle")
    assert await _code(session, gid, table.host, table.moon, "clue:sun") == (409, "seat_taken")
    await table.clients["sun"].get(f"/api/ishaara/games/{gid}/key")
    assert await _code(session, gid, table.host, table.sun, "clue:sun") == (409, "already_seated")


async def test_an_unknown_user_id_has_no_account(table: Table, session: AsyncSession) -> None:
    game = await _seated_game(table)
    with pytest.raises(ApiError) as error:
        await SeatInvitesService(session).create(
            kind="ishaara",
            game_id=uuid.UUID(game["id"]),
            host=table.host,
            to_user_id=uuid.uuid4(),
            seat="clue:sun",
        )
    assert (error.value.status, error.value.code.value) == (409, "no_account")


async def test_a_player_the_host_created_is_in_the_circle(
    table: Table, session: AsyncSession
) -> None:
    game = await _seated_game(table)
    friend = await make_user(session, email="friend@example.com", player_name="Friend")
    player = await make_player(session, name="Created by host", created_by=table.host.id)
    friend.player_id = player.id
    await session.flush()

    # In the circle, so the circle check passes and the seat rule answers.
    assert await _code(session, game["id"], table.host, friend, "clue:sun") == (409, "seat_taken")


def test_seat_invite_creates_are_limited_per_user() -> None:
    limit = rate_limit.SEAT_INVITE_CREATE
    assert (limit.name, limit.max_hits, limit.window_s) == ("seat-invite-create", 30, 3600)
