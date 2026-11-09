"""Rows of another game kind never reach Kachuful's endpoints and jobs (platform P8).

Ishaara stands in for a kind with no endpoints yet; Kaali Teeri has its own (test_kaali_teeri_*).
"""

import argparse
import copy
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SeatClaim, ShareLink, User
from app.domain.enums import GameKind
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError
from app.jobs import auto_end, prune_empty
from app.repositories.games import games_repository
from app.security.tokens import new_share_token
from app.services.games import GamesService
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload


async def _kachuful_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[User, dict[str, Any]]:
    """An admin scores an unfinished game at which they sit, with a guest who has no account."""
    owner = await make_user(session, email="admin@example.com", role="admin", player_name="Ada")
    guest = await make_player(session, name="Guest", created_by=owner.id)
    other = await make_player(session, name="Other")
    payload = make_payload(
        [str(owner.player_id), str(guest.id), str(other.id)],
        start_ms=to_ms(now_utc()) - 60 * MINUTE,
        finished=False,
    )
    await sign_in(client, owner)
    assert (await client.put(f"/api/games/{payload['syncId']}", json=payload)).status_code == 200
    return owner, payload


async def _copy_as(
    session: AsyncSession, source_id: str, kind: GameKind, **changes: Any
) -> uuid.UUID:
    """Insert a row of another kind straight into `games`, shaped like a stored Kachuful game."""
    source = await session.get(Game, uuid.UUID(source_id))
    assert source is not None
    columns = {column.key: getattr(source, column.key) for column in Game.__table__.columns}
    game_id = uuid.uuid4()
    columns |= {"id": game_id, "kind": kind.value, "payload": copy.deepcopy(source.payload)}
    session.add(Game(**(columns | changes)))
    await session.flush()
    return game_id


async def _listed_ids(client: httpx.AsyncClient, query: str) -> set[str]:
    response = await client.get(f"/api/games{query}")
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def test_lists_and_bootstrap_show_kachuful_games_only(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, payload = await _kachuful_game(client, session)
    await _copy_as(session, payload["syncId"], GameKind.KAALI_TEERI)
    await _copy_as(session, payload["syncId"], GameKind.ISHAARA)

    queries = ["", "?mine=true", "?involving=true", "?status=active", "?updatedSince=0"]
    listed = [await _listed_ids(client, query) for query in queries]
    bootstrap = (await client.get("/api/bootstrap")).json()

    assert listed == [{payload["syncId"]}] * len(queries)
    assert [game["id"] for game in bootstrap["myGames"]] == [payload["syncId"]]


async def test_another_kind_of_game_is_not_found_and_never_overwritten(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, payload = await _kachuful_game(client, session)
    foreign_id = await _copy_as(session, payload["syncId"], GameKind.ISHAARA)
    upload = copy.deepcopy(payload) | {"syncId": str(foreign_id), "rev": payload["rev"] + 1}

    got = await client.get(f"/api/games/{foreign_id}")
    put = await client.put(f"/api/games/{foreign_id}", json=upload)
    deleted = await client.delete(f"/api/games/{foreign_id}")
    link = await client.post(f"/api/games/{foreign_id}/links", json={"kind": "live"})

    assert got.status_code == 404
    assert (put.status_code, put.json()["code"]) == (409, "kind_mismatch")
    assert deleted.status_code == 404
    assert link.status_code == 404
    row = await games_repository.get(session, foreign_id, kind=None)
    assert row is not None and row.kind == "ishaara" and row.deleted_at is None
    assert row.rev == payload["rev"] and row.payload["syncId"] == payload["syncId"]


async def test_links_and_claims_on_another_kind_of_game_do_not_resolve(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner, payload = await _kachuful_game(client, session)
    foreign_id = await _copy_as(session, payload["syncId"], GameKind.ISHAARA)
    guest_id = next(s["playerId"] for s in payload["players"] if s["playerId"] != owner.player_id)
    tokens = {kind: new_share_token() for kind in ("live", "recap")}
    for kind, token in tokens.items():
        session.add(ShareLink(token=token, game_id=foreign_id, kind=kind, created_by=owner.id))
    claimant = await make_user(session, email="riya@example.com", player_name="Riya")
    session.add(
        SeatClaim(game_id=foreign_id, player_id=uuid.UUID(guest_id), claimant_user_id=claimant.id)
    )
    await session.flush()

    live = await client.get(f"/api/public/live/{tokens['live']}")
    recap = await client.get(f"/api/public/recap/{tokens['recap']}")
    decidable = await client.get("/api/claims")
    await sign_in(client, claimant)
    claim = await client.post(f"/api/games/{foreign_id}/claims", json={"playerId": guest_id})

    assert (live.status_code, recap.status_code) == (410, 410)
    assert decidable.status_code == 200 and decidable.json()["items"] == []
    assert claim.status_code == 404


async def test_admin_counts_and_clearing_data_leave_another_kind_alone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner, payload = await _kachuful_game(client, session)
    foreign_id = await _copy_as(session, payload["syncId"], GameKind.ISHAARA)

    overview = (await client.get("/api/admin/overview")).json()
    preview = (await client.get(f"/api/admin/users/{owner.id}/deletion-preview")).json()
    cleared = await client.delete("/api/me/data")

    assert overview["games"] == 1 and preview["games"] == 1
    assert cleared.status_code == 200 and cleared.json()["games"] == 1
    row = await games_repository.get(session, foreign_id, kind=GameKind.ISHAARA)
    assert row is not None and row.deleted_at is None


async def test_auto_end_and_prune_empty_skip_another_kind(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, payload = await _kachuful_game(client, session)
    idle_since = now_utc() - timedelta(days=3)
    idle = await _copy_as(
        session, payload["syncId"], GameKind.ISHAARA, client_updated_at=idle_since
    )
    empty = await _copy_as(session, payload["syncId"], GameKind.ISHAARA, round_count=0)
    before = {
        game_id: await games_repository.get(session, game_id, kind=None)
        for game_id in (idle, empty)
    }
    stamps = {game_id: (row.updated_at if row else None) for game_id, row in before.items()}

    await auto_end.run(argparse.Namespace())
    await prune_empty.run(argparse.Namespace(dry_run=False))

    for game_id in (idle, empty):
        row = await games_repository.get(session, game_id, kind=None)
        assert row is not None and row.ended_at is None and row.deleted_at is None
        assert row.updated_at == stamps[game_id]


async def test_rederiving_another_kind_of_game_is_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, payload = await _kachuful_game(client, session)
    foreign_id = await _copy_as(session, payload["syncId"], GameKind.ISHAARA)
    row = await games_repository.get(session, foreign_id, kind=None)
    assert row is not None

    with pytest.raises(ApiError) as caught:
        await GamesService(session).rederive(row, row.payload)

    assert (caught.value.status, caught.value.code) == (409, "kind_mismatch")
