"""`/api/kaali-teeri/games`: upload, read, list and delete with Kachuful's sync semantics."""

import copy
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, KtGamePlayer, KtHand, KtPlayerDay, Player, User
from app.domain.time import now_utc, to_ms
from tests.domain.golden import load, names
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload
from tests.kt_builder import Registry, fixture_payload, freeze_clock, new_id

INVALID = names("kt-invalid-")
BASE = "/api/kaali-teeri/games"


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch)


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


def _active(payload: dict[str, Any]) -> dict[str, Any]:
    """The same game, not finished yet."""
    out = copy.deepcopy(payload) | {"status": "active"}
    out.pop("finishedAt", None)
    return out


async def test_upload_stores_the_game_and_derives_every_table(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")

    response = await _put(client, payload)

    assert response.status_code == 200, response.text
    assert response.json() == {"rev": payload["rev"], "earned": []}
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None and row.kind == "kaali-teeri" and row.round_count == 4
    assert await _count(session, KtHand, payload["syncId"]) == 4
    assert await _count(session, KtGamePlayer, payload["syncId"]) == 4
    days = (await session.execute(select(func.count()).select_from(KtPlayerDay))).scalar_one()
    assert days >= 4


async def test_upload_counts_the_game_for_every_seated_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")

    await _put(client, payload)

    for player in registry.values():
        await session.refresh(player)
        assert player.games_count == 1 and player.last_played_at is not None


async def test_get_returns_payload_and_summary(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")
    await _put(client, payload)

    body = (await client.get(f"{BASE}/{payload['syncId']}")).json()

    assert body["game"] == payload
    summary = body["summary"]
    assert summary["kind"] == "kaali-teeri" and summary["status"] == "finished"
    assert summary["hands"] == 4 and summary["length"] == "endless"
    assert summary["createdAt"] == payload["createdAt"]
    assert summary["finishedAt"] == payload["finishedAt"]
    assert summary["owner"]["name"] == "Scorer sco" and summary["live"] is False
    assert {p["playerId"] for p in summary["players"]} == {str(p.id) for p in registry.values()}
    assert all(p["place"] is not None and p["photoUrl"] is None for p in summary["players"])


async def test_same_payload_again_is_a_no_op_and_an_older_rev_is_stale(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
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
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
    await _put(client, payload)
    newer = payload | {"rev": payload["rev"] + 1, "updatedAt": payload["updatedAt"] + 1}

    await _scorer(client, session, email="other@example.com")
    forbidden_put = await _put(client, newer)
    forbidden_delete = await client.delete(f"{BASE}/{payload['syncId']}")
    await _scorer(client, session, email="admin@example.com")
    admin = await session.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin is not None
    admin.role = "admin"
    await session.flush()
    admin_put = await _put(client, newer)

    assert (forbidden_put.status_code, forbidden_put.json()["code"]) == (403, "forbidden")
    assert (forbidden_delete.status_code, forbidden_delete.json()["code"]) == (403, "forbidden")
    assert admin_put.status_code == 200


async def test_a_deleted_game_is_gone_and_leaves_a_tombstone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
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
    assert await _count(session, KtHand, payload["syncId"]) == 0
    assert await _count(session, KtGamePlayer, payload["syncId"]) == 0
    days = (await session.execute(select(func.count()).select_from(KtPlayerDay))).scalar_one()
    assert days == 0


async def test_unknown_players_are_refused_and_merged_ones_remapped(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")
    unknown = copy.deepcopy(payload)
    stranger = str(uuid.uuid4())
    unknown["players"][1]["playerId"] = stranger
    target = registry["ben"]
    merged = Player(
        id=uuid.uuid4(),
        display_name="Old Ana",
        name_key="old ana",
        emoji="🙂",
        color="#ffffff",
        merged_into=target.id,
    )
    session.add(merged)
    await session.flush()
    remapped = new_id(payload)
    remapped["players"][1]["playerId"] = str(merged.id)
    remapped["players"][0]["playerId"] = str(registry["cy"].id)

    refused = await _put(client, unknown)
    # Seat 1 is a merged copy of ben and seat 0 moved to cy: two seats, two different players.
    remapped["players"][2]["playerId"] = str(registry["ana"].id)
    accepted = await _put(client, remapped)

    assert refused.status_code == 422
    assert refused.json()["code"] == "unknown_player"
    assert refused.json()["playerIds"] == [stranger]
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["remap"] == {str(merged.id): str(target.id)}
    stored = await session.get(Game, uuid.UUID(remapped["syncId"]))
    assert stored is not None and stored.payload["players"][1]["playerId"] == str(target.id)


async def test_a_seat_without_a_registry_id_fails_the_players_check(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
    del payload["players"][0]["playerId"]

    response = await _put(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_game" and response.json()["reason"] == "players"


@pytest.mark.parametrize("name", INVALID)
async def test_each_broken_rule_is_named_with_its_hand(
    client: httpx.AsyncClient, session: AsyncSession, name: str
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, name)
    expect = load(name)["expect"]

    response = await _put(client, payload)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "invalid_game"
    assert (body["reason"], body["handIndex"]) == (expect["reason"], expect["handIndex"])


async def test_a_malformed_payload_or_a_wrong_id_is_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
    no_kind = copy.deepcopy(payload)
    del no_kind["kind"]

    malformed = await _put(client, no_kind)
    wrong_id = await client.put(f"{BASE}/{uuid.uuid4()}", json=payload)

    assert (malformed.status_code, malformed.json()["code"]) == (422, "invalid")
    assert "kind" in malformed.json()["fields"]
    assert wrong_id.status_code == 422
    assert (wrong_id.json()["reason"], wrong_id.json()["handIndex"]) == ("id", None)


async def test_ids_of_the_other_game_are_kind_mismatch_both_ways(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    scorer = await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
    await _put(client, payload)
    kachuful = make_payload(
        [str(scorer.player_id), str(uuid.uuid4()), str(uuid.uuid4())],
        start_ms=payload["createdAt"] - 60 * MINUTE,
    )
    kachuful_id = kachuful["syncId"]
    kachuful["syncId"] = payload["syncId"]
    session.add(
        Game(
            id=uuid.UUID(kachuful_id),
            kind="kachuful",
            owner_id=scorer.id,
            rev=1,
            payload={},
            payload_hash="x",
            status="active",
            player_count=3,
            round_count=1,
            summary={},
            created_at=scorer.created_at,
            client_updated_at=scorer.created_at,
        )
    )
    await session.flush()

    kt_on_kachuful = await client.put(
        f"{BASE}/{kachuful_id}", json=payload | {"syncId": kachuful_id}
    )
    kachuful_on_kt = await client.put(f"/api/games/{payload['syncId']}", json=kachuful)
    get_other = await client.get(f"{BASE}/{kachuful_id}")
    delete_other = await client.delete(f"{BASE}/{kachuful_id}")

    assert (kt_on_kachuful.status_code, kt_on_kachuful.json()["code"]) == (409, "kind_mismatch")
    assert (kachuful_on_kt.status_code, kachuful_on_kt.json()["code"]) == (409, "kind_mismatch")
    assert get_other.status_code == 404 and delete_other.status_code == 404


async def test_a_game_with_no_hand_done_leaves_no_trace(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, _ = await fixture_payload(session, "kt-4p")
    await _put(client, payload)
    empty = _active(payload) | {"hands": [], "rev": payload["rev"] + 1}
    fresh = new_id(empty)

    emptied = await _put(client, empty)
    never = await _put(client, fresh)

    assert emptied.status_code == 200 and never.status_code == 200
    old = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert old is not None and old.deleted_at is not None
    assert await session.get(Game, uuid.UUID(fresh["syncId"])) is None


async def test_lists_filter_page_and_never_show_kachuful_games(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    registry: Registry = {}
    finished, registry = await fixture_payload(session, "kt-4p", registry)
    active = _active(new_id(finished))
    await _put(client, finished)
    await _put(client, active)
    other = await _scorer(client, session, email="other@example.com")
    theirs = new_id(finished)
    assert (await _put(client, theirs)).status_code == 200
    guests = [await make_player(session, name=n) for n in ("Guest One", "Guest Two")]
    kachuful = make_payload(
        [str(other.player_id), *(str(g.id) for g in guests)],
        start_ms=to_ms(now_utc()) - 60 * MINUTE,
    )
    assert (await client.put(f"/api/games/{kachuful['syncId']}", json=kachuful)).status_code == 200

    everything = (await client.get(BASE)).json()
    mine = (await client.get(f"{BASE}?mine=true")).json()
    running = (await client.get(f"{BASE}?status=active")).json()
    first = (await client.get(f"{BASE}?limit=2")).json()
    rest = (await client.get(f"{BASE}?limit=2&cursor={first['nextCursor']}")).json()
    bad = await client.get(f"{BASE}?cursor=nonsense")

    ids = {finished["syncId"], active["syncId"], theirs["syncId"]}
    assert {g["id"] for g in everything["items"]} == ids
    assert [g["id"] for g in mine["items"]] == [theirs["syncId"]]
    assert [g["id"] for g in running["items"]] == [active["syncId"]]
    assert len(first["items"]) == 2 and len(rest["items"]) == 1 and rest["nextCursor"] is None
    assert {g["id"] for g in first["items"] + rest["items"]} == ids
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid")


async def test_involving_lists_games_the_caller_sat_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")
    await _put(client, payload)
    seated = await make_user(session, email="ana@example.com", player_name=None)
    seated.player_id = registry["ana"].id
    outsider = await make_user(session, email="out@example.com", player_name="Outsider")
    await session.flush()

    await sign_in(client, seated)
    theirs = (await client.get(f"{BASE}?involving=true")).json()["items"]
    await sign_in(client, outsider)
    none = (await client.get(f"{BASE}?involving=true")).json()["items"]

    assert [g["id"] for g in theirs] == [payload["syncId"]]
    assert none == []


async def test_every_route_needs_a_signed_in_user(client: httpx.AsyncClient) -> None:
    game_id = uuid.uuid4()

    responses = [
        await client.get(BASE),
        await client.get(f"{BASE}/{game_id}"),
        await client.put(f"{BASE}/{game_id}", json={}),
        await client.delete(f"{BASE}/{game_id}"),
        await client.get("/api/kaali-teeri/bootstrap"),
        await client.get("/api/kaali-teeri/leaderboard"),
        await client.get(f"/api/kaali-teeri/players/{game_id}/stats"),
        await client.get(f"/api/kaali-teeri/players/{game_id}/games"),
        await client.get(f"/api/kaali-teeri/players/{game_id}/vs/{game_id}"),
    ]

    assert {r.status_code for r in responses} == {401}
