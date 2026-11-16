"""Spades boards, stats, head-to-head, player games and bootstrap over both kinds.

The set: four table-mode games (won, drawn, ended by the host, cancelled) and two finished online
tables (spades-classic, and the same game with Kabir replaced by Arjun after two hands).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import from_ms
from app.services.kt_boards import KtPeriod, period_days
from app.services.spades_games import SpadesGamesService
from app.settings import settings
from tests.factories import make_user, sign_in
from tests.spades_builder import (
    SP_NOW,
    Registry,
    case,
    finished_state,
    fixture_pid,
    freeze_clock,
    seat_registry,
    store_table,
)

API = "/api/spades"
P1, P2, P3, P4, P5 = (fixture_pid(n) for n in range(1, 6))


@dataclass(frozen=True)
class _Played:
    """One game of the set: its id, when it finished, and the winning team."""

    game_id: str
    finished_ms: int | None
    winner: str | None


@dataclass(frozen=True)
class _Set:
    games: list[_Played]
    registry: Registry


def _replaced_classic() -> dict[str, Any]:
    state = finished_state("spades-classic")
    for hand in state["history"][:2]:
        hand["players"][1] = P5
    return state


@pytest.fixture
async def the_set(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> _Set:
    """Every game of the set stored by one scorer, who is then signed in."""
    freeze_clock(monkeypatch)
    registry = await seat_registry(session)
    owner = await make_user(session, email="scorer@example.com", player_name="Scorer")
    service = SpadesGamesService(session)
    played: list[_Played] = []
    for name in ("won on target", "called a draw", "ended by the host, hand in progress dropped"):
        game = case(name)
        await service.upload(uuid.UUID(game["syncId"]), game, owner)
        finished = game.get("finishedAt") or game["updatedAt"]
        played.append(_Played(game["syncId"], finished, game["result"]["winner"]))
    cancelled = case("cancelled")
    await service.upload(uuid.UUID(cancelled["syncId"]), cancelled, owner)
    played.append(_Played(cancelled["syncId"], None, None))
    for state in (finished_state("spades-classic"), _replaced_classic()):
        await store_table(session, owner, state)
        played.append(_Played(state["id"], state["finishedAt"], state["result"]["winner"]))
    await sign_in(client, owner)
    return _Set(played, registry)


def _rows_by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["player"]["id"]: r for r in body["rows"]}


async def test_the_board_merges_both_kinds_and_counts_a_draw_as_played(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    body = (await client.get(f"{API}/leaderboard?period=all&sort=wins")).json()

    rows = _rows_by_id(body)
    assert (body["period"], body["sort"]) == ("all", "wins")
    assert set(rows) == {P1, P2, P3, P4, P5}
    for pid in (P1, P2, P3, P4):
        row = rows[pid]
        assert (row["games"], row["wins"], row["draws"], row["losses"]) == (5, 2, 1, 2), pid
        assert row["winRate"] == 0.4 and row["value"] == 2.0 and row["rank"] == 1
    # The cancelled game's hand counts nowhere; Kabir's two hands count, his game doesn't.
    assert rows[P1]["hands"] == 3 + 2 + 2 + 4 + 4
    assert rows[P2]["hands"] == 3 + 2 + 2 + 4 + 2
    kabir = rows[P5]
    assert (kabir["games"], kabir["hands"], kabir["winRate"], kabir["value"]) == (0, 2, None, 0.0)
    assert kabir["rank"] == 5 and body["rows"][-1]["player"]["id"] == P5


async def test_the_board_lists_the_best_pairs(client: httpx.AsyncClient, the_set: _Set) -> None:
    pairs = (await client.get(f"{API}/leaderboard")).json()["pairs"]

    assert [(p["a"]["id"], p["b"]["id"]) for p in pairs] == [(P1, P3), (P2, P4)]
    assert all((p["games"], p["wins"], p["draws"], p["winRate"]) == (5, 2, 1, 0.4) for p in pairs)


@pytest.mark.parametrize("sort", ["wins", "rate", "made"])
async def test_each_sort_ranks_by_its_value(
    client: httpx.AsyncClient, the_set: _Set, sort: str
) -> None:
    rows = (await client.get(f"{API}/leaderboard?sort={sort}")).json()["rows"]

    ranked = [r for r in rows if r["rank"] is not None]
    values = [r["value"] for r in ranked]
    assert values == sorted(values, reverse=True) and ranked[0]["rank"] == 1
    if sort == "rate":
        assert all(r["value"] == r["winRate"] for r in ranked)
        assert [r["player"]["id"] for r in rows if r["rank"] is None] == [P5]
    if sort == "made":
        assert all(r["value"] == r["madeRate"] for r in ranked)
        assert all(r["bids"] == 0 for r in rows if r["rank"] is None)


def _day(ms: int) -> date:
    return from_ms(ms).astimezone(settings.tz).date()


@pytest.mark.parametrize("period", ["today", "week", "season"])
async def test_period_boards_count_games_on_the_day_they_finished(
    client: httpx.AsyncClient, the_set: _Set, period: str
) -> None:
    first, end = period_days(KtPeriod(period), _day(SP_NOW))
    assert first is not None and end is not None
    counted = [
        g for g in the_set.games if g.finished_ms is not None and first <= _day(g.finished_ms) < end
    ]

    rows = _rows_by_id((await client.get(f"{API}/leaderboard?period={period}")).json())

    team_a, team_b = (P1, P3), (P2, P4)
    for pid in (*team_a, *team_b):
        team = "a" if pid in team_a else "b"
        games = len(counted)
        wins = sum(g.winner == team for g in counted)
        shown = rows.get(pid, {"games": 0, "wins": 0})
        assert (shown["games"], shown["wins"]) == (games, wins), (period, pid)


async def test_the_today_board_splits_the_set_at_midnight(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    # The set finished either side of midnight IST on 16/17 November.
    days = {_day(g.finished_ms) for g in the_set.games if g.finished_ms is not None}

    today = _rows_by_id((await client.get(f"{API}/leaderboard?period=today")).json())

    assert len(days) == 2
    assert 0 < today.get(P1, {"games": 0})["games"] < 5


async def test_player_stats_total_both_kinds_with_partners(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    riya = (await client.get(f"{API}/players/{P1}/stats")).json()
    kabir = (await client.get(f"{API}/players/{P5}/stats")).json()

    assert (riya["games"], riya["wins"], riya["draws"], riya["losses"]) == (5, 2, 1, 2)
    assert riya["hands"] == 15 and riya["bestScore"] == 240
    assert riya["partners"] == [{"playerId": P3, "games": 5, "wins": 2, "winRate": 0.4}]
    assert riya["madeRate"] == riya["bidsMade"] / riya["bids"]
    assert riya["bagsPerHand"] == riya["bags"] / riya["hands"]
    assert (kabir["games"], kabir["hands"], kabir["bestScore"], kabir["partners"]) == (
        0,
        2,
        None,
        [],
    )


async def test_head_to_head_splits_partners_and_opponents(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    partners = (await client.get(f"{API}/players/{P1}/vs/{P3}")).json()
    rivals = (await client.get(f"{API}/players/{P1}/vs/{P2}")).json()
    replaced = (await client.get(f"{API}/players/{P1}/vs/{P5}")).json()

    assert (partners["a"]["id"], partners["b"]["id"]) == (P1, P3)
    assert (partners["games"], partners["together"], partners["togetherWins"]) == (5, 5, 2)
    assert (rivals["games"], rivals["against"], rivals["aWins"], rivals["bWins"]) == (5, 5, 2, 2)
    assert (rivals["draws"], rivals["together"]) == (1, 0)
    assert replaced["games"] == 0


async def test_player_games_list_both_kinds_newest_first(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    expected = sorted((g.game_id for g in the_set.games), reverse=True)

    first = (await client.get(f"{API}/players/{P1}/games?limit=4")).json()
    rest = (await client.get(f"{API}/players/{P1}/games?cursor={first['nextCursor']}")).json()
    kabir = (await client.get(f"{API}/players/{P5}/games")).json()["items"]

    items = first["items"] + rest["items"]
    # Every game of the set began at the same moment, so the id breaks the tie.
    assert [g["id"] for g in items] == expected and rest["nextCursor"] is None
    online = [g for g in items if g["kind"] == "spades-online"]
    assert len(online) == 2 and all(g["mySeat"] == 0 and g["isHost"] is False for g in online)
    assert {g["kind"] for g in items} == {"spades", "spades-online"}
    assert [(g["kind"], g["mySeat"]) for g in kabir] == [("spades-online", None)]


async def test_boards_answer_304_when_nothing_changed(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    paths = [
        f"{API}/leaderboard",
        f"{API}/bootstrap",
        f"{API}/players/{P1}/stats",
        f"{API}/players/{P1}/vs/{P2}",
    ]

    for path in paths:
        first = await client.get(path)
        again = await client.get(path, headers={"If-None-Match": first.headers["ETag"]})
        assert (first.status_code, again.status_code) == (200, 304), path


async def test_bootstrap_lists_the_callers_table_mode_games_only(
    client: httpx.AsyncClient, session: AsyncSession, the_set: _Set
) -> None:
    mine = (await client.get(f"{API}/bootstrap")).json()
    await sign_in(client, await make_user(session, email="new@example.com"))
    theirs = (await client.get(f"{API}/bootstrap")).json()

    table_mode = {g.game_id for g in the_set.games[:4]}
    assert {g["id"] for g in mine["myGames"]} == table_mode
    assert all(g["kind"] == "spades" for g in mine["myGames"])
    statuses = sorted(g["status"] for g in mine["myGames"])
    assert statuses == ["cancelled", "finished", "finished", "finished"]
    assert theirs["myGames"] == [] and theirs["dataVersion"] == mine["dataVersion"]


async def test_deleting_a_game_takes_it_off_the_boards(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    won = the_set.games[0].game_id

    await client.delete(f"{API}/games/{won}")

    riya = _rows_by_id((await client.get(f"{API}/leaderboard")).json())[P1]
    assert (riya["games"], riya["wins"], riya["hands"]) == (4, 1, 12)


async def test_unknown_players_are_not_found(client: httpx.AsyncClient, the_set: _Set) -> None:
    nobody = uuid.uuid4()

    responses = [
        await client.get(f"{API}/players/{nobody}/stats"),
        await client.get(f"{API}/players/{nobody}/games"),
        await client.get(f"{API}/players/{nobody}/vs/{P1}"),
        await client.get(f"{API}/players/{P1}/vs/{nobody}"),
    ]

    assert {(r.status_code, r.json()["code"]) for r in responses} == {(404, "not_found")}


async def test_a_bad_period_sort_or_cursor_is_invalid(
    client: httpx.AsyncClient, the_set: _Set
) -> None:
    period = await client.get(f"{API}/leaderboard?period=year")
    sort = await client.get(f"{API}/leaderboard?sort=points")
    cursor = await client.get(f"{API}/players/{P1}/games?cursor=nonsense")

    assert (period.status_code, sort.status_code) == (422, 422)
    assert (cursor.status_code, cursor.json()["code"]) == (422, "invalid")


async def test_an_empty_board_has_no_rows(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await sign_in(client, await make_user(session, email="solo@example.com"))

    body = (await client.get(f"{API}/leaderboard")).json()

    assert (body["rows"], body["pairs"]) == ([], [])


async def test_boards_read_their_rows_in_one_query_and_never_a_payload(
    client: httpx.AsyncClient, the_set: _Set, queries: list[str]
) -> None:
    queries.clear()
    await client.get(f"{API}/leaderboard?period=week")
    board = [q for q in queries if "spades_game_players" in q]
    queries.clear()
    await client.get(f"{API}/players/{P1}/games?limit=1")
    one = len(queries)
    queries.clear()
    await client.get(f"{API}/players/{P1}/games")
    listed = list(queries)

    assert len(board) == 1
    assert not any("games.payload" in q for q in listed)
    # Six games cost what one does, plus the other kind's summary query (skipped with no ids).
    assert len(listed) == one + 1
