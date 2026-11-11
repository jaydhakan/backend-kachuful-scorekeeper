"""Kaali Teeri boards, stats, games, head-to-head and bootstrap, checked against the app's set."""

import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from itertools import pairwise
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kaali_teeri.game import kt_game_from_payload
from app.domain.kaali_teeri.stats import hand_rows, player_stats, stats_game
from app.domain.time import from_ms
from app.services import kt_boards
from app.services.kt_boards import KtPeriod, period_days
from app.services.kt_games import KtGamesService
from app.settings import settings
from tests.domain.golden import camel, load
from tests.factories import make_user, sign_in
from tests.kt_builder import Registry, freeze_clock, seat_registry, seated

API = "/api/kaali-teeri"


def _day(ms: int) -> date:
    return from_ms(ms).astimezone(settings.tz).date()


def _last_hand_ms(games: list[dict[str, Any]]) -> int:
    return max(h.get("doneAt") or g["updatedAt"] for g in games for h in g["hands"])


@pytest.fixture
async def the_set(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, Any]], Registry]:
    """Every game of `kt-set-stats` uploaded by one scorer, who is then signed in."""
    freeze_clock(monkeypatch)
    games = load("kt-set-stats")["games"]
    registry = await seat_registry(
        session, {s["playerId"] for g in games for s in g["players"]}, {}
    )
    owner = await make_user(session, email="scorer@example.com", player_name="Scorer")
    for game in games:
        payload = seated(game, registry)
        await KtGamesService(session).upload(uuid.UUID(payload["syncId"]), payload, owner)
    await sign_in(client, owner)
    return games, registry


def _without_partner_id(stats: dict[str, Any]) -> dict[str, Any]:
    """Stats minus mostPartner's id: ties break on the player key, and keys here are uuids."""
    partner = stats["mostPartner"]
    return {**stats, "mostPartner": partner and {**partner, "playerId": None}}


async def test_player_stats_match_the_app(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    games, registry = the_set
    expect = load("kt-set-stats")["expect"]["players"]
    stored = [stats_game(kt_game_from_payload(seated(g, registry))) for g in games]

    for key, player in registry.items():
        response = await client.get(f"{API}/players/{player.id}/stats")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == camel(asdict(player_stats(stored, str(player.id)))), key
        assert _without_partner_id(body) == _without_partner_id(expect[key]), key


async def test_head_to_head_matches_the_app(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    _, registry = the_set

    for pair in load("kt-set-stats")["expect"]["headToHead"]:
        a, b = registry[pair["a"]], registry[pair["b"]]
        response = await client.get(f"{API}/players/{a.id}/vs/{b.id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["a"]["id"], body["b"]["id"]) == (str(a.id), str(b.id))
        counts = {k: v for k, v in body.items() if k not in ("a", "b")}
        assert counts == {k: v for k, v in pair.items() if k not in ("a", "b")}


async def test_all_time_board_totals_match_the_players_stats(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    _, registry = the_set
    expect = load("kt-set-stats")["expect"]["players"]
    id_to_key = {str(p.id): k for k, p in registry.items()}

    rows = (await client.get(f"{API}/leaderboard?period=all&sort=points")).json()["rows"]

    assert len(rows) == len(registry)
    for row in rows:
        stats = expect[id_to_key[row["player"]["id"]]]
        assert row["points"] == stats["points"] and row["games"] == stats["games"]
        assert row["wins"] == stats["wins"] and row["handsBid"] == stats["handsBid"]
        assert row["bidsMade"] == stats["bidsMade"] and row["madeRate"] == stats["madeRate"]
    assert [r["value"] for r in rows] == sorted((r["value"] for r in rows), reverse=True)
    assert rows[0]["rank"] == 1


@pytest.mark.parametrize("sort", ["points", "wins", "made"])
async def test_each_sort_ranks_by_its_value(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry], sort: str
) -> None:
    rows = (await client.get(f"{API}/leaderboard?sort={sort}")).json()["rows"]

    ranked = [r for r in rows if r["rank"] is not None]
    values = [r["value"] for r in ranked]
    assert values == sorted(values, reverse=True)
    for prev, row in pairwise(ranked):
        assert row["rank"] == (
            prev["rank"] if row["value"] == prev["value"] else ranked.index(row) + 1
        )
    if sort == "made":
        assert all(r["madeRate"] == r["value"] for r in ranked)
        assert all(r["handsBid"] == 0 for r in rows if r["rank"] is None)


def _expected_points(
    games: list[dict[str, Any]], first: date, end: date, registry: Registry
) -> dict[str, int]:
    """Each player's points from the hands done in [first, end), worked out by the domain."""
    out: dict[str, int] = defaultdict(int)
    for raw in games:
        game = kt_game_from_payload(raw)
        for hand in hand_rows(game):
            if first <= _day(hand.done_at or game.updated_at) < end:
                for key, points in hand.scores.items():
                    out[str(registry[key].id)] += points
    return dict(out)


@pytest.mark.parametrize("period", ["today", "week", "season"])
async def test_period_boards_count_only_their_days(
    client: httpx.AsyncClient,
    the_set: tuple[list[dict[str, Any]], Registry],
    monkeypatch: pytest.MonkeyPatch,
    period: str,
) -> None:
    games, registry = the_set
    last = _last_hand_ms(games)
    monkeypatch.setattr(kt_boards, "now_utc", lambda: from_ms(last))
    first, end = period_days(KtPeriod(period), _day(last))
    assert first is not None and end is not None

    rows = (await client.get(f"{API}/leaderboard?period={period}")).json()["rows"]

    expected = _expected_points(games, first, end, registry)
    shown = {r["player"]["id"]: r["points"] for r in rows}
    assert {k: v for k, v in shown.items() if k in expected} == expected
    assert all(v == 0 for k, v in shown.items() if k not in expected)
    assert rows, "the last day of the set has hands"


async def test_boards_answer_304_when_nothing_changed(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    _, registry = the_set
    ana = registry["ana"].id
    paths = [
        f"{API}/leaderboard",
        f"{API}/bootstrap",
        f"{API}/players/{ana}/stats",
        f"{API}/players/{ana}/vs/{registry['ben'].id}",
    ]

    for path in paths:
        first = await client.get(path)
        again = await client.get(path, headers={"If-None-Match": first.headers["ETag"]})
        assert (first.status_code, again.status_code) == (200, 304), path


async def test_bootstrap_lists_the_callers_games_only(
    client: httpx.AsyncClient,
    session: AsyncSession,
    the_set: tuple[list[dict[str, Any]], Registry],
) -> None:
    games, _ = the_set

    mine = (await client.get(f"{API}/bootstrap")).json()
    await sign_in(client, await make_user(session, email="new@example.com"))
    theirs = (await client.get(f"{API}/bootstrap")).json()

    assert {g["id"] for g in mine["myGames"]} == {g["syncId"] for g in games}
    assert all(g["kind"] == "kaali-teeri" for g in mine["myGames"])
    assert theirs["myGames"] == [] and theirs["dataVersion"] == mine["dataVersion"]


async def test_player_games_page_newest_first(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    games, registry = the_set
    ana = registry["ana"].id
    sat = sorted(
        (g for g in games if any(s["playerId"] == "ana" for s in g["players"])),
        key=lambda g: (g["createdAt"], g["syncId"]),
        reverse=True,
    )

    first = (await client.get(f"{API}/players/{ana}/games?limit=5")).json()
    rest = (
        await client.get(f"{API}/players/{ana}/games?limit=200&cursor={first['nextCursor']}")
    ).json()

    listed = [g["id"] for g in first["items"] + rest["items"]]
    assert listed == [g["syncId"] for g in sat] and rest["nextCursor"] is None


async def test_unknown_players_are_not_found(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    _, registry = the_set
    nobody = uuid.uuid4()

    responses = [
        await client.get(f"{API}/players/{nobody}/stats"),
        await client.get(f"{API}/players/{nobody}/games"),
        await client.get(f"{API}/players/{nobody}/vs/{registry['ana'].id}"),
        await client.get(f"{API}/players/{registry['ana'].id}/vs/{nobody}"),
    ]

    assert {(r.status_code, r.json()["code"]) for r in responses} == {(404, "not_found")}


async def test_a_bad_period_or_sort_is_invalid(
    client: httpx.AsyncClient, the_set: tuple[list[dict[str, Any]], Registry]
) -> None:
    period = await client.get(f"{API}/leaderboard?period=year")
    sort = await client.get(f"{API}/leaderboard?sort=accuracy")

    assert (period.status_code, sort.status_code) == (422, 422)
