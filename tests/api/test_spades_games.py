"""`/api/spades/games`: table-mode upload, read, list and delete with Kaali Teeri's sync semantics."""

import copy
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Player, SpadesGamePlayer, SpadesSeat, User
from app.services.spades_games import SpadesGamesService
from tests.domain.golden import load
from tests.factories import make_user, sign_in
from tests.spades_builder import Registry, case, fixture_pid, freeze_clock, seat_registry

BASE = "/api/spades/games"
WON = "won on target"
# Invalid cases with a hand done: a game with none is never stored, so never checked.
INVALID = [
    c["name"]
    for c in load("spades-validate")["cases"]
    if c["expect"] is not None and any(h["phase"] == "done" for h in c["game"]["hands"])
]


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)


@pytest.fixture
async def registry(session: AsyncSession) -> Registry:
    return await seat_registry(session)


async def _scorer(
    client: httpx.AsyncClient, session: AsyncSession, email: str = "scorer@example.com"
) -> User:
    user = await make_user(session, email=email, player_name=f"Scorer {email[:3]}")
    await sign_in(client, user)
    return user


async def _put(client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
    return await client.put(f"{BASE}/{payload['syncId']}", json=payload)


async def _count(session: AsyncSession, model: Any, game_id: str) -> int:
    stmt = select(func.count()).select_from(model).where(model.game_id == uuid.UUID(game_id))
    return int((await session.execute(stmt)).scalar_one())


async def _results(session: AsyncSession, game_id: str) -> dict[str, str | None]:
    stmt = select(SpadesGamePlayer.player_id, SpadesGamePlayer.result).where(
        SpadesGamePlayer.game_id == uuid.UUID(game_id)
    )
    return {str(r[0]): r[1] for r in await session.execute(stmt)}


async def _kt_row(session: AsyncSession, owner: User) -> uuid.UUID:
    """A stored Kaali Teeri game's row (its contents don't matter here)."""
    game_id = uuid.uuid4()
    session.add(
        Game(
            id=game_id,
            kind="kaali-teeri",
            owner_id=owner.id,
            rev=1,
            payload={},
            payload_hash="x",
            status="active",
            player_count=4,
            round_count=1,
            summary={},
            created_at=owner.created_at,
            client_updated_at=owner.created_at,
        )
    )
    await session.flush()
    return game_id


async def test_upload_stores_the_game_its_seats_and_player_rows(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)

    response = await _put(client, payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rev"] == payload["rev"]
    # A finished game earns its winners' achievements (and everyone's first-game careers).
    assert body["earned"] and all(e["gameKind"] == "spades" for e in body["earned"])
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None and row.kind == "spades" and row.status == "finished"
    assert row.round_count == 3 and row.ended_at is not None
    assert await _count(session, SpadesSeat, payload["syncId"]) == 4
    assert await _results(session, payload["syncId"]) == {
        fixture_pid(1): "win",
        fixture_pid(2): "loss",
        fixture_pid(3): "win",
        fixture_pid(4): "loss",
    }
    for n in range(1, 5):
        player = registry[fixture_pid(n)]
        await session.refresh(player)
        assert player.games_count == 1 and player.last_played_at is not None


async def test_get_returns_payload_and_summary(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)

    body = (await client.get(f"{BASE}/{payload['syncId']}")).json()

    assert body["game"] == payload
    summary = body["summary"]
    assert summary["kind"] == "spades" and summary["status"] == "finished"
    assert (summary["hands"], summary["target"]) == (3, 200)
    assert summary["scores"] == {"a": 240, "b": -180}
    assert summary["result"] == {"winner": "a", "endedBy": "target"}
    assert (summary["createdAt"], summary["finishedAt"]) == (
        payload["createdAt"],
        payload["finishedAt"],
    )
    assert summary["owner"]["name"] == "Scorer sco" and summary["live"] is False
    assert [p["team"] for p in summary["players"]] == ["a", "b", "a", "b"]
    assert [p["playerId"] for p in summary["players"]] == [fixture_pid(n) for n in range(1, 5)]
    assert all(p["photoUrl"] is None for p in summary["players"])


async def test_an_active_game_has_seats_but_no_player_rows(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case("in progress, bids in order")

    assert (await _put(client, payload)).status_code == 200

    summary = (await client.get(f"{BASE}/{payload['syncId']}")).json()["summary"]
    assert (summary["status"], summary["hands"], summary["result"]) == ("active", 2, None)
    assert summary["finishedAt"] is None
    assert await _count(session, SpadesSeat, payload["syncId"]) == 4
    assert await _count(session, SpadesGamePlayer, payload["syncId"]) == 0


async def test_a_cancelled_game_is_a_finished_row_that_counts_nowhere(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case("cancelled")

    assert (await _put(client, payload)).status_code == 200

    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None and row.status == "finished"
    assert row.ended_at is not None and row.finished_at is None
    summary = (await client.get(f"{BASE}/{payload['syncId']}")).json()["summary"]
    assert (summary["status"], summary["result"]) == ("cancelled", None)
    assert await _count(session, SpadesGamePlayer, payload["syncId"]) == 0


async def test_a_called_draw_and_an_ended_game_store_their_results(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    draw = case("called a draw")
    ended = case("ended by the host, hand in progress dropped")

    assert (await _put(client, draw)).status_code == 200
    assert (await _put(client, ended)).status_code == 200

    assert set((await _results(session, draw["syncId"])).values()) == {"draw"}
    assert sorted((await _results(session, ended["syncId"])).values()) == [
        "loss",
        "loss",
        "win",
        "win",
    ]
    summary = (await client.get(f"{BASE}/{ended['syncId']}")).json()["summary"]
    assert summary["hands"] == 2 and summary["result"]["endedBy"] == "end"


async def test_same_payload_again_is_a_no_op_and_an_older_rev_is_stale(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)

    again = await _put(client, payload)
    changed = copy.deepcopy(payload)
    changed["players"][0]["emoji"] = "🐯"
    stale = await _put(client, changed)
    newer = await _put(client, changed | {"rev": payload["rev"] + 1})

    assert again.status_code == 200 and again.json()["rev"] == payload["rev"]
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale" and stale.json()["server"] == payload
    assert newer.status_code == 200 and newer.json()["rev"] == payload["rev"] + 1


async def test_only_the_scorer_or_an_admin_may_update_or_delete(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)
    newer = payload | {"rev": payload["rev"] + 1}

    await _scorer(client, session, email="other@example.com")
    forbidden_put = await _put(client, newer)
    forbidden_delete = await client.delete(f"{BASE}/{payload['syncId']}")
    admin = await _scorer(client, session, email="admin@example.com")
    admin.role = "admin"
    await session.flush()
    admin_put = await _put(client, newer)
    admin_delete = await client.delete(f"{BASE}/{payload['syncId']}")

    assert (forbidden_put.status_code, forbidden_put.json()["code"]) == (403, "forbidden")
    assert (forbidden_delete.status_code, forbidden_delete.json()["code"]) == (403, "forbidden")
    assert admin_put.status_code == 200 and admin_delete.status_code == 204


async def test_a_deleted_game_is_gone_and_leaves_a_tombstone(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)

    deleted = await client.delete(f"{BASE}/{payload['syncId']}")
    upload = await _put(client, payload | {"rev": payload["rev"] + 1})
    got = await client.get(f"{BASE}/{payload['syncId']}")
    again = await client.delete(f"{BASE}/{payload['syncId']}")
    tombstones = (await client.get(f"{BASE}?updatedSince=0")).json()["items"]

    assert deleted.status_code == 204
    assert (upload.status_code, upload.json()["code"]) == (410, "gone")
    assert got.status_code == 404 and again.status_code == 404
    assert tombstones[0]["id"] == payload["syncId"] and tombstones[0]["deleted"] is True
    assert await _count(session, SpadesSeat, payload["syncId"]) == 0
    assert await _count(session, SpadesGamePlayer, payload["syncId"]) == 0
    riya = registry[fixture_pid(1)]
    await session.refresh(riya)
    assert riya.games_count == 0


async def test_unknown_players_are_refused_and_merged_ones_remapped(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    unknown = case(WON)
    stranger = str(uuid.uuid4())
    unknown["players"][1]["playerId"] = stranger
    target = registry[fixture_pid(2)]
    merged = Player(
        id=uuid.uuid4(),
        display_name="Old Arjun",
        name_key="old arjun",
        emoji="🙂",
        color="#ffffff",
        merged_into=target.id,
    )
    session.add(merged)
    await session.flush()
    remapped = case(WON)
    remapped["players"][1]["playerId"] = str(merged.id)

    refused = await _put(client, unknown)
    accepted = await _put(client, remapped)

    assert (refused.status_code, refused.json()["code"]) == (422, "unknown_player")
    assert refused.json()["playerIds"] == [stranger]
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["remap"] == {str(merged.id): str(target.id)}
    stored = await session.get(Game, uuid.UUID(remapped["syncId"]))
    assert stored is not None and stored.payload["players"][1]["playerId"] == str(target.id)


async def test_a_seat_without_a_registry_id_fails_the_players_check(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    del payload["players"][0]["playerId"]

    response = await _put(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_game" and response.json()["reason"] == "players"


@pytest.mark.parametrize("name", INVALID)
async def test_each_broken_rule_is_named_with_its_hand(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry, name: str
) -> None:
    await _scorer(client, session)
    expect = next(c for c in load("spades-validate")["cases"] if c["name"] == name)["expect"]

    response = await _put(client, case(name))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "invalid_game"
    assert (body["reason"], body["handIndex"]) == (expect["reason"], expect["handIndex"])


async def test_a_malformed_payload_or_a_wrong_id_is_refused(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    no_kind = copy.deepcopy(payload)
    del no_kind["kind"]
    bad_status = payload | {"status": "abandoned"}

    malformed = await _put(client, no_kind)
    status = await _put(client, bad_status)
    wrong_id = await client.put(f"{BASE}/{uuid.uuid4()}", json=payload)

    assert (malformed.status_code, malformed.json()["code"]) == (422, "invalid")
    assert "kind" in malformed.json()["fields"]
    assert (status.status_code, status.json()["code"]) == (422, "invalid")
    assert wrong_id.status_code == 422
    assert (wrong_id.json()["reason"], wrong_id.json()["handIndex"]) == ("id", None)


async def test_ids_of_other_games_are_kind_mismatch_both_ways(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    scorer = await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)
    other_id = await _kt_row(session, scorer)
    kt = load("kt-4p")["game"] | {"syncId": payload["syncId"]}

    spades_on_kt = await client.put(f"{BASE}/{other_id}", json=payload | {"syncId": str(other_id)})
    kt_on_spades = await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=kt)
    get_other = await client.get(f"{BASE}/{other_id}")
    delete_other = await client.delete(f"{BASE}/{other_id}")

    assert (spades_on_kt.status_code, spades_on_kt.json()["code"]) == (409, "kind_mismatch")
    assert (kt_on_spades.status_code, kt_on_spades.json()["code"]) == (409, "kind_mismatch")
    assert get_other.status_code == 404 and delete_other.status_code == 404


async def test_a_game_with_no_hand_done_leaves_no_trace(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case("in progress, bids in order")
    await _put(client, payload)
    empty = payload | {"hands": payload["hands"][2:], "rev": payload["rev"] + 1}
    fresh = empty | {"syncId": str(uuid.uuid4())}

    emptied = await _put(client, empty)
    never = await _put(client, fresh)

    assert emptied.status_code == 200 and never.status_code == 200
    old = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert old is not None and old.deleted_at is not None
    assert await _count(session, SpadesSeat, payload["syncId"]) == 0
    assert await session.get(Game, uuid.UUID(fresh["syncId"])) is None


async def test_lists_filter_page_and_never_show_other_kinds(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    finished = case(WON)
    active = case("in progress, bids in order")
    await _put(client, finished)
    await _put(client, active)
    other = await _scorer(client, session, email="other@example.com")
    theirs = case("called a draw")
    assert (await _put(client, theirs)).status_code == 200
    await _kt_row(session, other)

    everything = (await client.get(BASE)).json()
    mine = (await client.get(f"{BASE}?mine=true")).json()
    running = (await client.get(f"{BASE}?status=active")).json()
    first = (await client.get(f"{BASE}?limit=2")).json()
    rest = (await client.get(f"{BASE}?limit=2&cursor={first['nextCursor']}")).json()
    bad = await client.get(f"{BASE}?cursor=nonsense")

    ids = {finished["syncId"], active["syncId"], theirs["syncId"]}
    assert {g["id"] for g in everything["items"]} == ids
    assert all(g["kind"] == "spades" for g in everything["items"])
    assert [g["id"] for g in mine["items"]] == [theirs["syncId"]]
    assert [g["id"] for g in running["items"]] == [active["syncId"]]
    assert len(first["items"]) == 2 and len(rest["items"]) == 1 and rest["nextCursor"] is None
    assert {g["id"] for g in first["items"] + rest["items"]} == ids
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid")


async def test_involving_lists_games_the_caller_sat_in(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)
    seated = await make_user(session, email="riya@example.com", player_name=None)
    seated.player_id = registry[fixture_pid(1)].id
    outsider = await make_user(session, email="out@example.com", player_name="Outsider")
    await session.flush()

    await sign_in(client, seated)
    theirs = (await client.get(f"{BASE}?involving=true")).json()["items"]
    await sign_in(client, outsider)
    none = (await client.get(f"{BASE}?involving=true")).json()["items"]

    assert [g["id"] for g in theirs] == [payload["syncId"]]
    assert none == []


async def test_reseat_moves_a_seat_and_its_rows(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    await _scorer(client, session)
    payload = case(WON)
    await _put(client, payload)
    source, target = registry[fixture_pid(1)], registry[fixture_pid(5)]
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None

    await SpadesGamesService(session).reseat(row, source.id, target.id)

    assert row.payload["players"][0]["playerId"] == str(target.id)
    results = await _results(session, payload["syncId"])
    assert results[str(target.id)] == "win" and str(source.id) not in results
    partner = await session.scalar(
        select(SpadesGamePlayer.partner_id).where(
            SpadesGamePlayer.game_id == row.id,
            SpadesGamePlayer.player_id == registry[fixture_pid(3)].id,
        )
    )
    assert partner == target.id
    for player, count in ((source, 0), (target, 1)):
        await session.refresh(player)
        assert player.games_count == count


async def test_delete_owned_removes_every_game_the_owner_scored(
    client: httpx.AsyncClient, session: AsyncSession, registry: Registry
) -> None:
    owner = await _scorer(client, session)
    first, second = case(WON), case("called a draw")
    await _put(client, first)
    await _put(client, second)

    deleted = await SpadesGamesService(session).delete_owned(owner, owner)

    assert deleted == 2
    for payload in (first, second):
        row = await session.get(Game, uuid.UUID(payload["syncId"]), populate_existing=True)
        assert row is not None and row.deleted_at is not None


async def test_every_route_needs_a_signed_in_user(client: httpx.AsyncClient) -> None:
    some_id = uuid.uuid4()

    responses = [
        await client.get(BASE),
        await client.get(f"{BASE}/{some_id}"),
        await client.put(f"{BASE}/{some_id}", json={}),
        await client.delete(f"{BASE}/{some_id}"),
        await client.get("/api/spades/bootstrap"),
        await client.get("/api/spades/leaderboard"),
        await client.get(f"/api/spades/players/{some_id}/stats"),
        await client.get(f"/api/spades/players/{some_id}/games"),
        await client.get(f"/api/spades/players/{some_id}/vs/{some_id}"),
    ]

    assert {r.status_code for r in responses} == {401}
