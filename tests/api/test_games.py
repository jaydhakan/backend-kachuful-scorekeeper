import copy
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, GamePlayer, Player, PlayerDay, RoundScore, User
from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload

HOUR = 60 * MINUTE


async def _table(session: AsyncSession, n: int = 3) -> list[Player]:
    return [await make_player(session, name=f"Seat{k}") for k in range(n)]


async def _scorer(client: httpx.AsyncClient, session: AsyncSession, **kw: Any) -> User:
    user = await make_user(session, **kw)
    await sign_in(client, user)
    return user


def _recent(player_ids: list[str], **kw: Any) -> dict[str, Any]:
    start = to_ms(now_utc()) - 2 * HOUR
    return make_payload(player_ids, start_ms=start, **kw)


async def _put(client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
    return await client.put(f"/api/games/{payload['syncId']}", json=payload)


async def _count(session: AsyncSession, model: Any, **where: Any) -> int:
    stmt = select(func.count()).select_from(model).filter_by(**where)
    return int((await session.execute(stmt)).scalar_one())


async def test_upload_stores_the_game_and_derives_every_table(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _scorer(client, session, player_name="Scorer")
    seats = await _table(session)
    payload = _recent([str(p.id) for p in seats], rounds=5)

    response = await _put(client, payload)

    assert response.status_code == 200
    assert response.json()["rev"] == 1
    codes = {a["code"] for a in response.json()["earned"]}
    assert {"winner", "first_win"} <= codes
    game_id = uuid.UUID(payload["syncId"])
    row = await session.get(Game, game_id)
    assert row is not None and row.owner_id == user.id and row.payload == payload
    assert row.ended_at is not None and row.auto_ended is False
    assert await _count(session, GamePlayer, game_id=game_id) == 3
    assert await _count(session, RoundScore, game_id=game_id) == 15
    assert await _count(session, PlayerDay) >= 3
    await session.refresh(seats[0])
    assert seats[0].games_count == 1
    assert seats[0].last_played_at is not None


async def test_get_game_returns_payload_summary_and_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session, player_name="Scorer")
    seats = await _table(session)
    payload = _recent([str(p.id) for p in seats])
    await _put(client, payload)

    response = await client.get(f"/api/games/{payload['syncId']}")

    assert response.status_code == 200
    body = response.json()
    assert body["game"] == payload
    summary = body["summary"]
    assert summary["id"] == payload["syncId"]
    assert summary["owner"]["name"] == "Scorer"
    assert summary["status"] == "finished"
    assert summary["endedAt"] == payload["finishedAt"]
    assert summary["rounds"] == 4
    assert summary["live"] is False
    assert {p["place"] for p in summary["players"]} <= {1, 2, 3}
    assert isinstance(body["achievements"], list)


async def test_same_payload_again_is_an_idempotent_retry(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)

    again = await _put(client, payload)

    assert again.status_code == 200
    assert again.json() == {"rev": 1, "earned": []}


@pytest.mark.parametrize("rev", [0, 1])
async def test_upload_with_older_or_equal_rev_and_changes_returns_stale(
    client: httpx.AsyncClient, session: AsyncSession, rev: int
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)
    changed = copy.deepcopy(payload)
    changed["rev"] = rev
    changed["ui"] = {"view": "summary", "bidIdx": 1}

    response = await _put(client, changed)

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert response.json()["server"] == payload


async def test_newer_rev_replaces_the_game_and_rebuilds_derived_rows(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    ids = [str(p.id) for p in await _table(session)]
    payload = _recent(ids, rounds=5, finished=False)
    await _put(client, payload)
    undone = copy.deepcopy(payload)
    undone["rev"] = 2
    undone["rounds"] = undone["rounds"][:3]

    response = await _put(client, undone)

    assert response.json()["rev"] == 2
    game_id = uuid.UUID(payload["syncId"])
    assert await _count(session, RoundScore, game_id=game_id) == 9
    rounds = (await session.execute(select(func.sum(PlayerDay.rounds)))).scalar_one()
    assert rounds == 9


async def test_another_member_cannot_overwrite_but_the_admin_can(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)
    newer = {**payload, "rev": 2}
    client.cookies.clear()
    await _scorer(client, session, email="other@example.com", player_name="Other")

    as_member = await _put(client, newer)
    client.cookies.clear()
    await _scorer(client, session, email="admin@example.com", role="admin", player_name="Adm")
    as_admin = await _put(client, newer)

    assert as_member.status_code == 403
    assert as_admin.status_code == 200


async def test_upload_with_unregistered_players_returns_unknown_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    seats = await _table(session, 2)
    stranger = str(uuid.uuid4())
    payload = _recent([str(seats[0].id), str(seats[1].id), stranger])

    response = await _put(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_player"
    assert response.json()["playerIds"] == [stranger]


async def test_upload_with_a_merged_player_rewrites_the_payload_and_reports_remap(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    seats = await _table(session)
    target = await make_player(session, name="Target")
    seats[0].merged_into = target.id
    await session.flush()
    payload = _recent([str(p.id) for p in seats])

    response = await _put(client, payload)

    assert response.status_code == 200
    assert response.json()["remap"] == {str(seats[0].id): str(target.id)}
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None and row.payload["players"][0]["playerId"] == str(target.id)
    retry = await _put(client, payload)
    assert retry.status_code == 200


async def test_upload_of_a_broken_game_returns_invalid_game_with_reason_and_round(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    payload["rounds"][2]["dealerIndex"] = (payload["rounds"][2]["dealerIndex"] + 1) % 3

    response = await _put(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_game"
    assert response.json()["reason"] == "round_plan"
    assert response.json()["roundIndex"] == 2


async def test_upload_to_a_different_path_id_returns_invalid_game_id(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])

    response = await client.put(f"/api/games/{uuid.uuid4()}", json=payload)

    assert response.status_code == 422
    assert response.json()["reason"] == "id"


async def test_upload_of_a_malformed_payload_returns_invalid_with_fields(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    game_id = str(uuid.uuid4())

    response = await client.put(f"/api/games/{game_id}", json={"id": game_id, "rev": "x"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"
    assert "rev" in response.json()["fields"]


async def test_upload_of_a_deleted_game_returns_gone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)
    await client.delete(f"/api/games/{payload['syncId']}")

    response = await _put(client, {**payload, "rev": 5})

    assert response.status_code == 410
    assert response.json()["code"] == "gone"


async def test_game_routes_need_a_session(client: httpx.AsyncClient) -> None:
    game_id = uuid.uuid4()

    responses = [
        await client.put(f"/api/games/{game_id}", json={}),
        await client.get("/api/games"),
        await client.get(f"/api/games/{game_id}"),
        await client.delete(f"/api/games/{game_id}"),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]


async def test_an_old_idle_game_is_auto_ended_at_its_last_round(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    start = to_ms(now_utc()) - 48 * HOUR
    payload = make_payload(
        [str(p.id) for p in await _table(session)], start_ms=start, finished=False
    )

    await _put(client, payload)

    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None and row.auto_ended is True
    assert to_ms(row.ended_at) == payload["rounds"][-1]["doneAt"]  # type: ignore[arg-type]


async def test_list_games_is_newest_first_pages_and_never_reads_payloads(
    client: httpx.AsyncClient, session: AsyncSession, queries: list[str]
) -> None:
    await _scorer(client, session)
    ids = [str(p.id) for p in await _table(session)]
    uploaded = [_recent(ids, seed=k) for k in range(3)]
    for payload in uploaded:
        await _put(client, payload)
    queries.clear()

    first = await client.get("/api/games", params={"limit": 2})
    second = await client.get(
        "/api/games", params={"limit": 2, "cursor": first.json()["nextCursor"]}
    )

    listed = [g["id"] for g in first.json()["items"] + second.json()["items"]]
    assert listed == [p["syncId"] for p in reversed(uploaded)]
    assert second.json()["nextCursor"] is None
    assert not any("payload" in q and "games" in q for q in queries)


async def test_list_games_mine_filters_to_my_uploads(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    ids = [str(p.id) for p in await _table(session)]
    await _put(client, _recent(ids, seed=1))
    client.cookies.clear()
    await _scorer(client, session, email="b@example.com", player_name="B")
    mine = _recent(ids, seed=2)
    await _put(client, mine)

    everyone = await client.get("/api/games")
    only_mine = await client.get("/api/games", params={"mine": "true"})

    assert len(everyone.json()["items"]) == 2
    assert [g["id"] for g in only_mine.json()["items"]] == [mine["syncId"]]


async def test_list_games_active_returns_only_unfinished_games_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    ids = [str(p.id) for p in await _table(session)]
    await _put(client, _recent(ids, seed=1))
    old_idle = make_payload(ids, start_ms=to_ms(now_utc()) - 48 * HOUR, finished=False, seed=2)
    await _put(client, old_idle)
    deleted = _recent(ids, seed=3, finished=False)
    await _put(client, deleted)
    await client.delete(f"/api/games/{deleted['syncId']}")
    older = _recent(ids, seed=4, finished=False)
    await _put(client, older)
    newest = _recent(ids, seed=5, finished=False)
    await _put(client, newest)

    response = await client.get("/api/games", params={"status": "active"})

    items = response.json()["items"]
    assert [g["id"] for g in items] == [newest["syncId"], older["syncId"]]
    assert all(g["status"] == "active" and g["endedAt"] is None for g in items)


async def test_list_games_active_and_mine_gives_my_newest_game_to_resume(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await _scorer(client, session)
    ids = [str(p.id) for p in await _table(session)]
    await _put(client, _recent(ids, seed=1, finished=False))
    my_newest = _recent(ids, seed=2, finished=False)
    await _put(client, my_newest)
    client.cookies.clear()
    await _scorer(client, session, email="b@example.com", player_name="B")
    await _put(client, _recent(ids, seed=3, finished=False))
    client.cookies.clear()
    await sign_in(client, me)

    response = await client.get(
        "/api/games", params={"status": "active", "mine": "true", "limit": 1}
    )

    body = response.json()
    assert [g["id"] for g in body["items"]] == [my_newest["syncId"]]
    assert body["nextCursor"] is not None


async def test_list_games_with_an_unknown_status_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)

    response = await client.get("/api/games", params={"status": "paused"})

    assert response.status_code == 422
    assert "status" in response.json()["fields"]


async def test_list_games_since_includes_tombstones_for_deleted_games(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)
    await client.delete(f"/api/games/{payload['syncId']}")

    plain = await client.get("/api/games")
    since = await client.get("/api/games", params={"updatedSince": 0})

    assert plain.json()["items"] == []
    tombstone = since.json()["items"][0]
    assert tombstone["id"] == payload["syncId"]
    assert tombstone["deleted"] is True
    assert isinstance(tombstone["updatedAt"], int)


async def test_delete_removes_derived_rows_and_hides_the_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    seats = await _table(session)
    payload = _recent([str(p.id) for p in seats])
    await _put(client, payload)

    response = await client.delete(f"/api/games/{payload['syncId']}")

    assert response.status_code == 204
    assert (await client.get(f"/api/games/{payload['syncId']}")).status_code == 404
    assert await _count(session, RoundScore) == 0
    assert await _count(session, PlayerDay) == 0
    await session.refresh(seats[0])
    assert seats[0].games_count == 0
    assert (await client.delete(f"/api/games/{payload['syncId']}")).status_code == 404


async def test_delete_by_another_member_is_forbidden(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    await _put(client, payload)
    client.cookies.clear()
    await _scorer(client, session, email="x@example.com", player_name="X")

    response = await client.delete(f"/api/games/{payload['syncId']}")

    assert response.status_code == 403


async def test_delete_my_games_removes_only_the_callers_games(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    seats = [str(p.id) for p in await _table(session)]
    await _scorer(client, session, email="other@example.com", player_name="Other")
    others = _recent(seats)
    await _put(client, others)
    client.cookies.clear()
    await _scorer(client, session, player_name="Me")
    mine = [_recent(seats) for _ in range(2)]
    for payload in mine:
        await _put(client, payload)

    response = await client.delete("/api/me/games")

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    for payload in mine:
        assert (await client.get(f"/api/games/{payload['syncId']}")).status_code == 404
    assert (await client.get(f"/api/games/{others['syncId']}")).status_code == 200
    assert await _count(session, RoundScore, game_id=uuid.UUID(others["syncId"])) > 0
    assert await _count(session, RoundScore) == await _count(
        session, RoundScore, game_id=uuid.UUID(others["syncId"])
    )


async def test_delete_my_games_is_a_no_op_when_there_are_none(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)

    response = await client.delete("/api/me/games")

    assert response.status_code == 200
    assert response.json() == {"deleted": 0}


async def test_delete_my_games_needs_a_session(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/me/games")

    assert response.status_code == 401


async def test_admin_overview_counts_users_players_games_and_rounds(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session, role="admin", email="a@example.com")
    await _put(client, _recent([str(p.id) for p in await _table(session)], rounds=6))

    response = await client.get("/api/admin/overview")

    assert response.json() == {
        "users": 1,
        "players": 4,
        "games": 1,
        "rounds": 6,
        "lastBackupAt": None,
    }


async def test_uploads_over_120_a_minute_are_rate_limited(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload = _recent([str(p.id) for p in await _table(session)])
    for _ in range(120):
        await _put(client, payload)

    response = await _put(client, payload)

    assert response.status_code == 429
