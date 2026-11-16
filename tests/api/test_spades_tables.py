"""Spades online tables through the API: create, find, join, rules, view, list, QR and delete."""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SpadesTableSeat
from app.services.spades_tables import CODE_ALPHABET
from tests.factories import make_user
from tests.ishaara_builder import (
    ClientFor,
    client_for_fixture,  # noqa: F401 - fixture
)
from tests.spades_tables_builder import (
    RULES,
    Clock,
    Seats,
    create,
    freeze,
    make_seats,
    ok,
    seated,
    started,
    view,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return freeze(monkeypatch)


@pytest.fixture
async def seats(session: AsyncSession, client_for: ClientFor, clock: Clock) -> Seats:
    return await make_seats(session, client_for)


def _error(response: httpx.Response, status: int, code: str, reason: str | None = None) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    if reason is not None:
        assert body["reason"] == reason


async def test_create_seats_the_host_and_gives_a_code(seats: Seats) -> None:
    out = await create(seats)

    table = out["table"]
    assert table["kind"] == "spades-online" and table["status"] == "lobby"
    assert table["name"] == "Riya's table" and table["rules"] == RULES
    assert len(table["code"]) == 4 and set(table["code"]) <= set(CODE_ALPHABET)
    assert table["seats"][0]["who"]["playerId"] == str(seats.users[0].player_id)
    assert out["me"] == {"seat": 0, "host": True, "hand": None}
    assert out["invites"] == [None, None, None, None]
    assert isinstance(out["serverNow"], int)


async def test_create_again_with_the_same_id_returns_the_table(seats: Seats) -> None:
    out = await create(seats)
    body = {"id": seats.table, "rules": RULES, "name": "Other"}

    again = await seats.clients[0].post("/api/spades/tables", json=body)
    taken = await seats.clients[1].post("/api/spades/tables", json=body)

    assert again.status_code == 200 and again.json()["table"]["code"] == out["table"]["code"]
    _error(taken, 409, "id_conflict")


async def test_create_refuses_bad_rules_a_bad_name_and_no_player(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    bad = await seats.clients[0].post(
        "/api/spades/tables", json={"id": str(uuid.uuid4()), "rules": RULES | {"target": 55}}
    )
    long = await seats.clients[0].post(
        "/api/spades/tables", json={"id": str(uuid.uuid4()), "rules": RULES, "name": "x" * 41}
    )
    loner = await client_for(await make_user(session, email="no@example.com", player_name=None))
    none = await loner.post("/api/spades/tables", json={"id": str(uuid.uuid4()), "rules": RULES})

    _error(bad, 422, "invalid")
    _error(long, 422, "invalid")
    _error(none, 409, "no_account")


async def test_routes_need_sign_in(client: httpx.AsyncClient) -> None:
    gid = uuid.uuid4()
    responses = [
        await client.post("/api/spades/tables", json={"id": str(gid), "rules": RULES}),
        await client.get(f"/api/spades/tables/{gid}"),
        await client.get("/api/spades/tables"),
        await client.get("/api/spades/tables/by-code/ABCD"),
        await client.get(f"/api/spades/tables/{gid}/events"),
    ]

    assert {r.status_code for r in responses} == {401}


async def test_by_code_finds_an_open_table_only(seats: Seats) -> None:
    out = await create(seats)
    code = out["table"]["code"]

    found = await seats.clients[1].get(f"/api/spades/tables/by-code/{code.lower()}")
    missing = await seats.clients[1].get("/api/spades/tables/by-code/ZZZZ")
    await ok(seats, 0, "cancelGame")
    closed = await seats.clients[1].get(f"/api/spades/tables/by-code/{code}")

    assert found.status_code == 200 and found.json() == {"id": seats.table}
    _error(missing, 404, "not_found")
    _error(closed, 404, "not_found")


async def test_join_takes_the_lowest_seat_a_named_seat_and_moves(seats: Seats) -> None:
    await create(seats)
    path = f"{seats.path}/join"

    lowest = await seats.clients[1].post(path, json={})
    named = await seats.clients[2].post(path, json={"seat": 3})
    moved = await seats.clients[1].post(path, json={"seat": 2})

    assert lowest.json()["me"]["seat"] == 1
    assert named.json()["me"]["seat"] == 3
    assert moved.json()["me"]["seat"] == 2
    assert moved.json()["table"]["seats"][1]["who"] is None


async def test_join_errors(seats: Seats, session: AsyncSession, client_for: ClientFor) -> None:
    await create(seats)
    path = f"{seats.path}/join"
    await seats.clients[1].post(path, json={"seat": 1})
    taken = await seats.clients[2].post(path, json={"seat": 1})
    bad = await seats.clients[2].post(path, json={"seat": 7})
    await seats.clients[2].post(path, json={})
    await seats.clients[3].post(path, json={})
    fifth = await client_for(await make_user(session, email="e@example.com", player_name="Eve"))
    full = await fifth.post(path, json={})
    unknown = await fifth.post(f"/api/spades/tables/{uuid.uuid4()}/join", json={})

    _error(taken, 409, "seat_taken")
    _error(bad, 422, "invalid_action", "bad-seat")
    _error(full, 409, "full")
    assert full.json()["game"]["me"]["seat"] is None
    _error(unknown, 404, "not_found")


async def test_join_after_the_start_is_refused(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    await started(seats)
    late = await client_for(await make_user(session, email="l@example.com", player_name="Late"))

    response = await late.post(f"{seats.path}/join", json={})

    _error(response, 422, "invalid_action", "started")


async def test_rules_host_only_and_lobby_only(seats: Seats) -> None:
    await create(seats)
    await seats.clients[1].post(f"{seats.path}/join", json={})
    path = f"{seats.path}/rules"
    changed = await seats.clients[0].patch(
        path, json={"rules": RULES | {"target": 300}, "name": "  Friday  "}
    )
    guest = await seats.clients[1].patch(path, json={"rules": RULES})
    bad = await seats.clients[0].patch(path, json={"rules": RULES | {"timerSec": 45}})

    assert changed.status_code == 200
    assert changed.json()["table"]["rules"]["target"] == 300
    assert changed.json()["table"]["name"] == "Friday"
    _error(guest, 403, "not_your_turn")
    _error(bad, 422, "invalid_action", "bad-rules")


async def test_rules_after_the_start_are_refused(seats: Seats) -> None:
    await started(seats)

    response = await seats.clients[0].patch(f"{seats.path}/rules", json={"rules": RULES})

    _error(response, 422, "invalid_action", "started")


async def test_each_viewer_sees_only_their_own_hand(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    await started(seats)
    stranger = await client_for(await make_user(session, email="s@example.com", player_name="Sam"))

    views = [await view(seats, s) for s in range(4)]
    watcher = (await stranger.get(seats.path)).json()

    suits = ["spades", "hearts", "diamonds", "clubs"]
    for seat, v in enumerate(views):
        assert v["me"]["seat"] == seat
        assert len(v["me"]["hand"]) == 13
        assert {c["suit"] for c in v["me"]["hand"]} == {suits[seat]}
        assert '"rank"' not in json.dumps({k: x for k, x in v["table"].items() if k != "drawn"})
    assert watcher["me"] == {"seat": None, "host": False, "hand": None}
    assert '"rank"' not in json.dumps({k: x for k, x in watcher["table"].items() if k != "drawn"})


async def test_the_payload_never_holds_a_hand(seats: Seats, session: AsyncSession) -> None:
    await started(seats)

    stmt = select(Game.payload, Game.summary).where(Game.id == uuid.UUID(seats.table))
    payload, summary = (await session.execute(stmt)).one()

    # Only the public draw for deal holds cards before a hand is played.
    assert '"rank"' not in json.dumps({k: v for k, v in payload.items() if k != "drawn"})
    assert '"rank"' not in json.dumps(summary)
    assert "userId" not in json.dumps(summary)


async def test_get_unknown_or_deleted_table_is_404(seats: Seats) -> None:
    await create(seats)
    await ok(seats, 0, "cancelGame")
    await seats.clients[0].delete(seats.path)

    gone = await seats.clients[0].get(seats.path)
    unknown = await seats.clients[0].get(f"/api/spades/tables/{uuid.uuid4()}")

    _error(gone, 404, "not_found")
    _error(unknown, 404, "not_found")


async def test_list_shows_my_tables_by_status(
    seats: Seats, session: AsyncSession, client_for: ClientFor, clock: Clock
) -> None:
    await seated(seats)
    first = seats.table
    clock.advance(1)
    await create(seats)
    await ok(seats, 0, "cancelGame")
    stranger = await client_for(await make_user(session, email="s@example.com", player_name="Sam"))

    active = (await seats.clients[1].get("/api/spades/tables?status=active")).json()
    mine = (await seats.clients[0].get("/api/spades/tables?status=finished")).json()
    everything = (await seats.clients[0].get("/api/spades/tables?limit=1")).json()
    nobody = (await stranger.get("/api/spades/tables")).json()

    assert [t["id"] for t in active["items"]] == [first]
    row: dict[str, Any] = active["items"][0]
    assert row["mySeat"] == 1 and row["isHost"] is False and row["status"] == "lobby"
    assert all("userId" not in str(s) for s in row["seats"])
    assert [t["id"] for t in mine["items"]] == [seats.table]
    assert mine["items"][0]["status"] == "cancelled" and mine["items"][0]["isHost"] is True
    assert len(everything["items"]) == 1 and everything["nextCursor"] is not None
    nxt = (
        await seats.clients[0].get(f"/api/spades/tables?limit=1&cursor={everything['nextCursor']}")
    ).json()
    assert [t["id"] for t in nxt["items"]] == [first]
    assert nobody == {"items": [], "nextCursor": None}


async def test_qr_encodes_the_join_link_for_seated_people_only(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    await create(seats)
    stranger = await client_for(await make_user(session, email="s@example.com", player_name="Sam"))

    svg = await seats.clients[0].get(f"{seats.path}/qr.svg")
    refused = await stranger.get(f"{seats.path}/qr.svg")
    unknown = await seats.clients[0].get(f"/api/spades/tables/{uuid.uuid4()}/qr.svg")

    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"].startswith("private")
    assert svg.text.lstrip().startswith("<svg")
    _error(refused, 404, "not_found")
    _error(unknown, 404, "not_found")


async def test_delete_needs_the_host_and_an_ended_table(
    seats: Seats, session: AsyncSession
) -> None:
    await seated(seats)
    open_ = await seats.clients[0].delete(seats.path)
    await ok(seats, 0, "cancelGame")
    guest = await seats.clients[1].delete(seats.path)
    done = await seats.clients[0].delete(seats.path)

    _error(open_, 422, "invalid_action", "not-finished")
    _error(guest, 403, "forbidden")
    assert done.status_code == 204
    rows = (
        await session.execute(
            select(SpadesTableSeat).where(SpadesTableSeat.game_id == uuid.UUID(seats.table))
        )
    ).all()
    assert rows == []


async def test_seat_rows_follow_the_lobby(seats: Seats, session: AsyncSession) -> None:
    await seated(seats)
    await ok(seats, 0, "shufflePartners")

    rows = (
        await session.execute(
            select(SpadesTableSeat.seat, SpadesTableSeat.user_id).where(
                SpadesTableSeat.game_id == uuid.UUID(seats.table)
            )
        )
    ).all()
    table = (await view(seats, 0))["table"]

    by_seat = {seat: str(user) for seat, user in rows}
    assert by_seat == {i: s["who"]["userId"] for i, s in enumerate(table["seats"])}


async def test_list_reads_summaries_in_fixed_queries(
    seats: Seats, clock: Clock, queries: list[str]
) -> None:
    for _ in range(3):
        await create(seats)
        clock.advance(1)
    queries.clear()

    page = (await seats.clients[0].get("/api/spades/tables")).json()

    assert len(page["items"]) == 3
    games = [q for q in queries if "FROM games" in q]
    assert len(games) == 1 and "payload" not in games[0]
