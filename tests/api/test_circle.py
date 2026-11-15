"""`GET /api/me/circle`: who I can invite, last played first, and nobody else's lastSeenAt."""

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player
from app.domain.time import now_utc, to_ms
from app.repositories.circle import members_stmt
from tests.factories import make_player, make_user
from tests.ishaara_builder import (
    ClientFor,
    Table,
    client_for_fixture,  # noqa: F401 - fixture
    create,
    make_table,
)


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


async def _circle(client: httpx.AsyncClient) -> list[dict[str, object]]:
    response = await client.get("/api/me/circle")
    assert response.status_code == 200, response.text
    items: list[dict[str, object]] = response.json()["items"]
    return items


async def test_circle_is_who_i_created_and_sat_with_who_have_accounts(
    table: Table, session: AsyncSession, client_for: ClientFor
) -> None:
    await create(table, players=table.seats(host_seated=True))
    friend = await make_user(session, email="friend@example.com", player_name=None)
    made = await make_player(session, name="Made By Host", created_by=table.host.id)
    friend.player_id = made.id
    inactive = await make_user(session, email="off@example.com", player_name=None, active=False)
    inactive.player_id = (await make_player(session, name="Off", created_by=table.host.id)).id
    stranger = await make_user(session, email="who@example.com", player_name="Stranger")
    seen = now_utc().replace(microsecond=0)
    for user, minutes in ((table.sun, 1), (stranger, 0)):
        user.last_seen_at = seen - timedelta(minutes=minutes)
    for user, days in ((table.sun, 2), (table.moon, 1)):
        player = await session.get(Player, user.player_id)
        assert player is not None
        player.last_played_at = seen - timedelta(days=days)
    await session.flush()

    items = await _circle(table.clients["host"])

    assert [i["userId"] for i in items] == [str(u.id) for u in (table.moon, table.sun, friend)]
    sun = items[1]
    assert sun == {
        "userId": str(table.sun.id),
        "playerId": str(table.sun.player_id),
        "name": "Sunny",
        "emoji": "🦊",
        "color": "#ff8a65",
        "photoUrl": None,
        "lastSeenAt": to_ms(seen - timedelta(minutes=1)),
        "lastPlayedAt": to_ms(seen - timedelta(days=2)),
    }
    assert str(stranger.id) not in json.dumps(items)


async def test_a_circle_is_never_someone_elses(
    table: Table, session: AsyncSession, client_for: ClientFor
) -> None:
    await create(table, players=table.seats(host_seated=True))
    outsider = await client_for(
        await make_user(session, email="out@example.com", player_name="Out")
    )

    assert await _circle(outsider) == []
    # Sun sat with the host, so each is in the other's circle (never played yet: by name); the
    # host's guests have no account.
    assert [i["userId"] for i in await _circle(table.clients["sun"])] == [
        str(table.host.id),
        str(table.moon.id),
    ]


async def test_the_circle_query_reads_only_indexes(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    stmt = members_stmt(uuid.uuid4(), uuid.uuid4())
    sql = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})

    plan = json.dumps((await session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))).scalar_one())

    assert "Seq Scan" not in plan, plan
    for index in (
        "ix_players_created_by",
        "ix_game_players_player_id",
        "ix_kt_game_players_player_id",
        "ix_ishaara_roles_player_id",
    ):
        assert index in plan, plan
