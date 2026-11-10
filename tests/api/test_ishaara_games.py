"""Ishaara create, read, key, list and delete: happy paths, every error code and permission."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraKey, IshaaraRole
from app.security import rate_limit
from tests.factories import make_player, make_user
from tests.ishaara_builder import (
    ClientFor,
    Table,
    client_for_fixture,  # noqa: F401 - fixture
    create,
    hides_the_key,
    make_table,
    read,
    start,
)


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


async def test_create_deals_a_key_apart_from_the_public_state(
    table: Table, session: AsyncSession
) -> None:
    response = await table.clients["host"].post("/api/ishaara/games", json=table.body())

    assert response.status_code == 201, response.text
    out = response.json()
    game = out["game"]
    assert out["me"] == {"host": True, "team": None}
    assert game["status"] == "waiting" and game["rev"] == 1
    assert game["revealed"] == [None] * 25 and game["solution"] is None
    assert game["turn"] == game["starting"]
    key = (await session.execute(select(IshaaraKey.key))).scalar_one()
    counts = {t: key.count(t) for t in ("sun", "moon", "blank", "trap")}
    second_team = "moon" if game["starting"] == "sun" else "sun"
    assert counts == {game["starting"]: 9, second_team: 8, "blank": 7, "trap": 1}
    assert hides_the_key(response.text, game, key)
    roles = (await session.execute(select(IshaaraRole))).scalars().all()
    assert {(r.team, r.role, r.user_id) for r in roles if r.role == "clue-giver"} == {
        ("sun", "clue-giver", table.sun.id),
        ("moon", "clue-giver", table.moon.id),
    }
    row = await session.get(Game, uuid.UUID(game["id"]))
    assert row is not None and row.kind == "ishaara" and row.status == "active"
    assert row.round_count == 0


async def test_resending_the_same_id_returns_the_game(table: Table) -> None:
    body = table.body()
    first = await table.clients["host"].post("/api/ishaara/games", json=body)
    again = await table.clients["host"].post("/api/ishaara/games", json=body)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["game"] == first.json()["game"]


async def test_an_id_used_by_another_host_or_kind_is_an_id_conflict(table: Table) -> None:
    body = table.body()
    await table.clients["host"].post("/api/ishaara/games", json=body)

    taken = await table.clients["sun"].post("/api/ishaara/games", json=body)

    assert (taken.status_code, taken.json()["code"]) == (409, "id_conflict")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"words": ["apple"] * 25}, "duplicate-words"),
        ({"words": ["apple"]}, "words"),
        ({"words": [f"w{k}" for k in range(25)]}, "words"),
        ({"rules": {"timerSec": 45, "clueEntry": "typed", "packs": [], "bestOf": 1}}, "rules"),
    ],
)
async def test_bad_words_and_rules_are_invalid_game(
    table: Table, change: dict[str, object], reason: str
) -> None:
    response = await table.clients["host"].post("/api/ishaara/games", json=table.body(**change))

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_game",
        "message": response.json()["message"],
        "reason": reason,
    }


async def test_seats_are_checked_by_the_domain(table: Table) -> None:
    seats = table.seats()
    one_team = [{**s, "team": "sun"} for s in seats]
    no_giver = [{**s, "role": "guesser"} for s in seats]

    teams = await table.clients["host"].post(
        "/api/ishaara/games", json=table.body(players=one_team)
    )
    givers = await table.clients["host"].post(
        "/api/ishaara/games", json=table.body(players=no_giver)
    )

    assert teams.json()["reason"] == "teams"
    assert givers.json()["reason"] == "clue-givers"


async def test_a_clue_giver_without_an_account_is_refused(
    table: Table, session: AsyncSession
) -> None:
    seats = table.seats()
    guest = await make_player(session, name="No Account")
    seats[0] = {**seats[0], "playerId": str(guest.id)}

    response = await table.clients["host"].post(
        "/api/ishaara/games", json=table.body(players=seats)
    )

    assert (response.status_code, response.json()["reason"]) == (422, "clue-giver-account")


async def test_an_unregistered_player_is_unknown_player(table: Table) -> None:
    seats = table.seats()
    stranger = str(uuid.uuid4())
    seats[2] = {**seats[2], "playerId": stranger}

    response = await table.clients["host"].post(
        "/api/ishaara/games", json=table.body(players=seats)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "unknown_player"
    assert response.json()["playerIds"] == [stranger]


async def test_a_malformed_body_is_invalid(table: Table) -> None:
    response = await table.clients["host"].post(
        "/api/ishaara/games", json={"id": "nope", "players": [], "words": []}
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid")


async def test_creating_needs_a_session(client: httpx.AsyncClient, table: Table) -> None:
    response = await client.post("/api/ishaara/games", json=table.body())

    assert response.status_code == 401


async def test_creates_are_rate_limited_per_user(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limit, "ISHAARA_CREATE", rate_limit.Limit("ishaara-create", 2, 3600))
    codes = [
        (await table.clients["host"].post("/api/ishaara/games", json=table.body())).status_code
        for _ in range(3)
    ]

    assert codes == [201, 201, 429]


async def test_members_and_the_admin_read_the_game_and_others_get_404(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    game = (await create(table))["game"]
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    stranger = await make_user(session, email="who@example.com", player_name="Who")
    path = f"/api/ishaara/games/{game['id']}"

    sun = await table.clients["sun"].get(path)
    seen_by_admin = await (await client_for(admin)).get(path)
    unseen = await (await client_for(stranger)).get(path)
    missing = await table.clients["host"].get(f"/api/ishaara/games/{uuid.uuid4()}")

    assert sun.status_code == 200 and sun.json()["me"] == {"host": False, "team": "sun"}
    assert seen_by_admin.status_code == 200
    assert seen_by_admin.json()["me"] == {"host": False, "team": None}
    assert (unseen.status_code, unseen.json()["code"]) == (404, "not_found")
    assert missing.status_code == 404


async def test_a_seated_guesser_with_an_account_is_a_member(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    guesser = await make_user(session, email="g@example.com", player_name="Guessy")
    seats = table.seats()
    seats[2] = {**seats[2], "playerId": str(guesser.player_id)}
    game = (await create(table, players=seats))["game"]

    seen = await (await client_for(guesser)).get(f"/api/ishaara/games/{game['id']}")
    key = await (await client_for(guesser)).get(f"/api/ishaara/games/{game['id']}/key")

    assert seen.status_code == 200 and seen.json()["me"] == {"host": False, "team": None}
    assert (key.status_code, key.json()["code"]) == (403, "forbidden")


async def test_each_clue_giver_gets_the_key_and_joins(table: Table, session: AsyncSession) -> None:
    game = (await create(table))["game"]
    path = f"/api/ishaara/games/{game['id']}/key"

    sun = await table.clients["sun"].get(path)
    after_sun = await read(table, game["id"])
    again = await table.clients["sun"].get(path)
    after_again = await read(table, game["id"])
    moon = await table.clients["moon"].get(path)
    after_moon = await read(table, game["id"])

    stored = (await session.execute(select(IshaaraKey.key))).scalar_one()
    assert sun.status_code == 200 and sun.json() == {"key": stored, "team": "sun"}
    assert sun.headers["cache-control"] == "no-store"
    assert moon.json() == {"key": stored, "team": "moon"}
    assert (after_sun["rev"], after_sun["joined"], after_sun["status"]) == (
        2,
        {"sun": True, "moon": False},
        "waiting",
    )
    assert again.status_code == 200 and after_again["rev"] == 2
    assert (after_moon["rev"], after_moon["status"]) == (3, "active")


async def test_only_the_two_clue_giver_accounts_get_the_key(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    game = (await create(table))["game"]
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    stranger = await make_user(session, email="who@example.com", player_name="Who")
    path = f"/api/ishaara/games/{game['id']}/key"

    host = await table.clients["host"].get(path)
    by_admin = await (await client_for(admin)).get(path)
    by_stranger = await (await client_for(stranger)).get(path)

    assert (host.status_code, host.json()["code"]) == (403, "forbidden")
    assert (by_admin.status_code, by_admin.json()["code"]) == (403, "forbidden")
    assert by_stranger.status_code == 404
    for response in (host, by_admin, by_stranger):
        assert "key" not in response.json()
    assert (await read(table, game["id"]))["rev"] == 1


async def test_a_host_who_gives_clues_gets_the_key(table: Table) -> None:
    seats = table.seats()
    seats[0] = {**seats[0], "playerId": str(table.host.player_id)}
    out = await create(table, players=seats)

    key = await table.clients["host"].get(f"/api/ishaara/games/{out['game']['id']}/key")

    assert out["me"] == {"host": True, "team": "sun"}
    assert key.status_code == 200 and key.json()["team"] == "sun"


async def test_lists_show_summaries_for_the_caller_only(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
    queries: list[str],
) -> None:
    waiting = (await create(table))["game"]
    active, key = await start(table)
    stranger = await make_user(session, email="who@example.com", player_name="Who")
    queries.clear()

    hosted = (await table.clients["host"].get("/api/ishaara/games")).json()
    sun = (await table.clients["sun"].get("/api/ishaara/games?role=clue-giver")).json()
    running = (await table.clients["moon"].get("/api/ishaara/games?status=active")).json()
    done = (await table.clients["moon"].get("/api/ishaara/games?status=finished")).json()
    nothing = (await (await client_for(stranger)).get("/api/ishaara/games")).json()

    assert [g["id"] for g in hosted["items"]] == [active["id"], waiting["id"]]
    assert hosted["nextCursor"] is None
    first = hosted["items"][0]
    assert first["status"] == "active" and first["myTeam"] is None
    assert first["host"] == {"name": "Host"} and first["revealed"] == 0
    second_team = "moon" if active["starting"] == "sun" else "sun"
    assert first["wordsLeft"] == {active["starting"]: 9, second_team: 8}
    assert {p["role"] for p in first["players"]} == {"clue-giver", "guesser"}
    assert [g["myTeam"] for g in sun["items"]] == ["sun", "sun"]
    assert len(running["items"]) == 2 and done["items"] == []
    assert nothing["items"] == []
    assert not any("payload" in q for q in queries if "FROM games" in q)
    assert all(hides_the_key(str(hosted), g, key) for g in [active])


async def test_list_pages_with_a_cursor(table: Table) -> None:
    ids = [(await create(table))["game"]["id"] for _ in range(3)]

    first = (await table.clients["host"].get("/api/ishaara/games?limit=2")).json()
    second = (
        await table.clients["host"].get(f"/api/ishaara/games?limit=2&cursor={first['nextCursor']}")
    ).json()
    bad = await table.clients["host"].get("/api/ishaara/games?cursor=nonsense")

    assert [g["id"] for g in first["items"] + second["items"]] == ids[::-1]
    assert second["nextCursor"] is None
    assert bad.status_code == 422


async def test_involving_is_any_player_for_the_admin_and_own_games_for_members(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    game = (await create(table))["game"]
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    stranger = await make_user(session, email="who@example.com", player_name="Who")
    query = f"/api/ishaara/games?involving={table.guests[0].id}"

    by_admin = (await (await client_for(admin)).get(query)).json()
    by_stranger = (await (await client_for(stranger)).get(query)).json()
    by_moon = (await table.clients["moon"].get(query)).json()

    assert [g["id"] for g in by_admin["items"]] == [game["id"]]
    assert by_stranger["items"] == []
    assert [g["id"] for g in by_moon["items"]] == [game["id"]]


async def test_the_host_or_admin_deletes_and_the_game_is_gone_everywhere(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    game = (await create(table))["game"]
    other = (await create(table))["game"]
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    stranger = await make_user(session, email="who@example.com", player_name="Who")

    by_sun = await table.clients["sun"].delete(f"/api/ishaara/games/{game['id']}")
    by_stranger = await (await client_for(stranger)).delete(f"/api/ishaara/games/{game['id']}")
    deleted = await table.clients["host"].delete(f"/api/ishaara/games/{game['id']}")
    by_admin = await (await client_for(admin)).delete(f"/api/ishaara/games/{other['id']}")

    assert (by_sun.status_code, by_sun.json()["code"]) == (403, "forbidden")
    assert by_stranger.status_code == 404
    assert deleted.status_code == 204 and by_admin.status_code == 204
    assert (await table.clients["host"].get(f"/api/ishaara/games/{game['id']}")).status_code == 404
    key = await table.clients["sun"].get(f"/api/ishaara/games/{game['id']}/key")
    assert key.status_code == 404
    listed = (await table.clients["host"].get("/api/ishaara/games")).json()
    assert listed["items"] == []
    again = await table.clients["host"].delete(f"/api/ishaara/games/{game['id']}")
    assert again.status_code == 404
    seats = await session.execute(select(func.count()).select_from(IshaaraRole))
    assert seats.scalar_one() == 0
