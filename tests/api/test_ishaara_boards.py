"""Ishaara boards through the API, against the app's `ishaara-set-boards.json` (parity)."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraGamePlayer, Player
from app.domain.time import from_ms
from tests.domain.golden import load
from tests.factories import make_player, make_user, sign_in

Registry = dict[str, Player]


async def _seed(session: AsyncSession) -> Registry:
    """Every fixture row stored as a finished game's ishaara_game_players row."""
    fixture = load("ishaara-set-boards")
    owner = await make_user(session, email="host@example.com", player_name="Host")
    registry: Registry = {}
    games: dict[str, uuid.UUID] = {}
    for row in fixture["rows"]:
        if row["playerId"] not in registry:
            registry[row["playerId"]] = await make_player(session, name=f"I-{len(registry)}")
        if row["gameId"] not in games:
            games[row["gameId"]] = await _game(session, owner.id, row["finishedAt"])
    await session.execute(
        insert(IshaaraGamePlayer),
        [
            {
                "game_id": games[r["gameId"]],
                "player_id": registry[r["playerId"]].id,
                "team": r["team"],
                "role": r["role"],
                "won": r["won"],
                "words_found": r["wordsFound"],
                "wrong_picks": r["wrongPicks"],
                "trap_hit": r["trapHit"],
                "clues_given": r["cluesGiven"],
                "avg_clue_number": r["avgClueNumber"],
                "penalties": r["penalties"],
                "finished_at": from_ms(r["finishedAt"]),
            }
            for r in fixture["rows"]
        ],
    )
    return registry


async def _game(session: AsyncSession, owner_id: uuid.UUID, finished_ms: int) -> uuid.UUID:
    at = from_ms(finished_ms)
    game = Game(
        id=uuid.uuid4(),
        kind="ishaara",
        owner_id=owner_id,
        rev=1,
        payload={},
        payload_hash="x",
        status="finished",
        player_count=4,
        round_count=9,
        summary={},
        created_at=at,
        client_updated_at=at,
        finished_at=at,
    )
    session.add(game)
    await session.flush()
    return game.id


def _close(got: Any, want: Any) -> bool:
    if isinstance(want, float) or isinstance(got, float):
        return got is not None and want is not None and abs(got - want) < 1e-9
    return bool(got == want)


def _matches(got: dict[str, Any], want: dict[str, Any], skip: set[str]) -> bool:
    return all(_close(got[k], v) for k, v in want.items() if k not in skip)


@pytest.fixture
async def signed_in(client: httpx.AsyncClient, session: AsyncSession) -> Registry:
    registry = await _seed(session)
    viewer = await make_user(session, email="viewer@example.com", player_name="Viewer")
    await sign_in(client, viewer)
    return registry


@pytest.mark.parametrize("sort", ["wins", "rate", "found"])
async def test_the_leaderboard_matches_the_app(
    client: httpx.AsyncClient, signed_in: Registry, sort: str
) -> None:
    by_id = {str(p.id): key for key, p in signed_in.items()}
    want = load("ishaara-set-boards")["expect"]["leaderboard"][sort]

    response = await client.get(f"/api/ishaara/leaderboard?period=all&sort={sort}")

    assert response.status_code == 200 and response.headers["etag"]
    body = response.json()
    assert (body["period"], body["sort"]) == ("all", sort)
    rows = body["rows"]
    assert [by_id[r["player"]["id"]] for r in rows] == [w["playerId"] for w in want]
    for got, expected in zip(rows, want, strict=True):
        assert _matches(got, expected, {"playerId"}), (got, expected)


async def test_a_period_counts_games_by_the_day_they_finished(
    client: httpx.AsyncClient, signed_in: Registry
) -> None:
    today = (await client.get("/api/ishaara/leaderboard?period=today")).json()

    assert today["rows"] == []


async def test_the_leaderboard_answers_304_while_nothing_changed(
    client: httpx.AsyncClient, signed_in: Registry
) -> None:
    first = await client.get("/api/ishaara/leaderboard")
    again = await client.get(
        "/api/ishaara/leaderboard", headers={"If-None-Match": first.headers["etag"]}
    )
    bad = await client.get("/api/ishaara/leaderboard?sort=points")

    assert again.status_code == 304
    assert bad.status_code == 422


async def test_player_stats_are_the_domain_tally(
    client: httpx.AsyncClient, signed_in: Registry
) -> None:
    tallies = load("ishaara-set-boards")["expect"]["tallies"]

    for want in tallies:
        response = await client.get(f"/api/ishaara/players/{signed_in[want['playerId']].id}/stats")
        assert response.status_code == 200
        assert _matches(response.json(), want, {"playerId"}), (response.json(), want)


async def test_stats_of_a_player_with_no_games_are_zero_and_unknown_is_404(
    client: httpx.AsyncClient, signed_in: Registry, session: AsyncSession
) -> None:
    fresh = await make_player(session, name="Fresh")

    zero = await client.get(f"/api/ishaara/players/{fresh.id}/stats")
    missing = await client.get(f"/api/ishaara/players/{uuid.uuid4()}/stats")

    assert zero.json()["games"] == 0 and zero.json()["avgClueNumber"] is None
    assert missing.status_code == 404


async def test_head_to_head_matches_the_app(client: httpx.AsyncClient, signed_in: Registry) -> None:
    pairs = load("ishaara-set-boards")["expect"]["headToHead"]

    for want in pairs:
        a, b = signed_in[want["a"]].id, signed_in[want["b"]].id
        response = await client.get(f"/api/ishaara/players/{a}/vs/{b}")
        assert response.status_code == 200
        body = response.json()
        assert (body["a"]["id"], body["b"]["id"]) == (str(a), str(b))
        assert _matches(body, want, {"a", "b"}), (body, want)
    missing = await client.get(f"/api/ishaara/players/{uuid.uuid4()}/vs/{a}")
    assert missing.status_code == 404


async def test_boards_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/ishaara/leaderboard")).status_code == 401
