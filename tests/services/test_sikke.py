"""Sikke's reconcile after every write: finishes, wins, streaks, achievements and bounties.

Recomputing is idempotent and keeps `earned_at`; deletes and void games take their entries back.
"""

import argparse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Player, SikkeEntry
from app.domain.enums import ProgressionGame
from app.domain.sikke import achievement_sikke
from app.domain.time import from_ms, now_utc, to_ms
from app.jobs import recompute_sikke
from app.jobs.fake_games import MINUTE, make_payload
from app.services.awards import AwardsService
from app.services.merge import MergeService
from app.services.sikke import SikkeService
from tests.factories import make_player, make_user, sign_in
from tests.spades_builder import case, seat_registry
from tests.spades_builder import freeze_clock as freeze_spades

HOUR = 60 * MINUTE
TIE_SEED = 10
"""Three players, four rounds: the first two seats tie for first."""


async def _table(
    client: httpx.AsyncClient, session: AsyncSession, n: int = 3, prefix: str = "S"
) -> list[str]:
    user = await make_user(session, email=f"{prefix.lower()}@example.com", player_name=prefix)
    await sign_in(client, user)
    return [str((await make_player(session, name=f"{prefix}{k}")).id) for k in range(n)]


async def _put(client: httpx.AsyncClient, ids: list[str], start_ms: int, **kw: Any) -> str:
    payload = make_payload(ids, start_ms=start_ms, **kw)
    response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return str(payload["syncId"])


async def _entries(session: AsyncSession, player_id: str) -> list[SikkeEntry]:
    stmt = (
        select(SikkeEntry)
        .where(SikkeEntry.player_id == uuid.UUID(player_id))
        .order_by(SikkeEntry.earned_at, SikkeEntry.key)
    )
    return list((await session.execute(stmt)).scalars())


async def _keys(session: AsyncSession, player_id: str, kind: str) -> list[str]:
    return [e.key for e in await _entries(session, player_id) if e.kind == kind]


async def _snapshot(session: AsyncSession) -> list[tuple[object, ...]]:
    stmt = select(
        SikkeEntry.id,
        SikkeEntry.player_id,
        SikkeEntry.key,
        SikkeEntry.amount,
        SikkeEntry.earned_at,
        SikkeEntry.data,
    ).order_by(SikkeEntry.id)
    return [tuple(r) for r in await session.execute(stmt)]


async def test_a_finished_game_pays_finish_win_streak_and_its_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    game = await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=3)
    winners = {
        str(p)
        for p in (
            await session.execute(
                select(Achievement.player_id).where(
                    Achievement.game_id == uuid.UUID(game), Achievement.code == "winner"
                )
            )
        ).scalars()
    }

    for pid in ids:
        entries = await _entries(session, pid)
        kinds = {e.kind: e for e in entries}
        assert kinds["finish"].amount == 30 and kinds["finish"].key == f"finish:{game}"
        assert ("win" in kinds) == (pid in winners)
        assert kinds["streak"].amount == 10 and kinds["streak"].data == {"weeks": 1}
        held = (
            await session.execute(
                select(Achievement).where(Achievement.player_id == uuid.UUID(pid))
            )
        ).scalars()
        paying = {f"ach:{a.id}" for a in held if achievement_sikke(ProgressionGame(a.game), a.code)}
        assert {e.key for e in entries if e.kind == "achievement"} == paying


async def test_tied_winners_each_get_win(client: httpx.AsyncClient, session: AsyncSession) -> None:
    ids = await _table(client, session)
    await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=4, seed=TIE_SEED)

    assert [len(await _keys(session, pid, "win")) for pid in ids] == [1, 1, 0]


async def test_recomputing_twice_changes_nothing_and_keeps_earned_at(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    await _put(client, ids, to_ms(now_utc()) - 3 * HOUR, rounds=6, seed=5)
    await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=6)
    marked = datetime(2026, 1, 1, tzinfo=UTC)
    await session.execute(update(SikkeEntry).values(earned_at=marked))
    before = await _snapshot(session)
    service = SikkeService(session)

    for _ in range(2):
        await service.recompute_players({uuid.UUID(p) for p in ids})
        await service.recompute_bounties()

    assert await _snapshot(session) == before
    assert {row[4] for row in before} == {marked}


async def test_every_write_leaves_the_ledger_a_full_recompute_would(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A write pays only its new achievement rows and replays finishes when a game ends or goes:
    at every step of a game's life that equals rebuilding each player from scratch."""
    ids = await _table(client, session)
    players = {uuid.UUID(p) for p in ids}
    start = to_ms(now_utc()) - 3 * HOUR
    await _put(client, ids, start - 3 * HOUR, rounds=6, seed=5)
    service = SikkeService(session)
    game: str | None = None
    paid: set[str] = set()

    async def matches_a_rebuild() -> None:
        before = await _snapshot(session)
        await service.recompute_players(players)
        assert await _snapshot(session) == before

    # Running writes that earn achievements, the end, then an edit after it ended (rev 4).
    for rev, rounds, finished in [(1, 3, False), (2, 6, False), (3, 6, True), (4, 4, True)]:
        game = await _put(
            client, ids, start, rounds=rounds, seed=8, finished=finished, game_id=game, rev=rev
        )
        await matches_a_rebuild()
        paid |= {e.kind for pid in ids for e in await _entries(session, pid)}
    assert {"achievement", "finish", "win", "streak"} <= paid

    assert (await client.delete(f"/api/games/{game}")).status_code == 204
    await matches_a_rebuild()


async def test_deleting_an_early_game_pays_the_milestone_it_moves(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """With eleven ended games, deleting the third moves `games_10` onto the eleventh: the new row is paid, as a full
    rebuild would pay it."""
    ids = await _table(client, session)
    start = to_ms(now_utc()) - 30 * HOUR
    games = [
        await _put(client, ids, start + k * 2 * HOUR, rounds=3, seed=20 + k) for k in range(11)
    ]
    before = {pid: await _keys(session, pid, "achievement") for pid in ids}

    assert (await client.delete(f"/api/games/{games[2]}")).status_code == 204

    moved = (
        (
            await session.execute(
                select(Achievement.id).where(
                    Achievement.code == "games_10", Achievement.game_id == uuid.UUID(games[10])
                )
            )
        )
        .scalars()
        .all()
    )
    assert moved, "games_10 should now sit on the eleventh game"
    paid = {k for pid in ids for k in await _keys(session, pid, "achievement")}
    assert {f"ach:{a}" for a in moved} <= paid
    assert before  # the table had achievements before the delete
    snapshot = await _snapshot(session)
    await SikkeService(session).recompute_players({uuid.UUID(p) for p in ids})
    assert await _snapshot(session) == snapshot


async def test_an_unfinished_game_pays_no_finish_until_it_ends(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    start = to_ms(now_utc()) - 2 * HOUR
    payload = make_payload(ids, start_ms=start, rounds=5, seed=4, finished=False)
    await client.put(f"/api/games/{payload['syncId']}", json=payload)
    running = {e.kind for pid in ids for e in await _entries(session, pid)}

    done = make_payload(ids, start_ms=start, rounds=5, seed=4, game_id=payload["syncId"], rev=2)
    await client.put(f"/api/games/{payload['syncId']}", json=done)

    assert running <= {"achievement"}
    for pid in ids:
        assert await _keys(session, pid, "finish")


async def test_deleting_a_game_takes_its_entries_back(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    game = await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=3)

    assert (await client.delete(f"/api/games/{game}")).status_code == 204

    assert [await _entries(session, pid) for pid in ids] == [[], [], []]


async def test_finish_and_win_pay_for_eight_games_of_a_game_a_day(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    # 2026-11-10 from 06:00 Asia/Kolkata, a game every 30 minutes: one APP_TZ day.
    day = to_ms(datetime(2026, 11, 10, 0, 30, tzinfo=UTC))
    games = [await _put(client, ids, day + k * 30 * MINUTE, rounds=2, seed=k) for k in range(9)]

    paid = [k.removeprefix("finish:") for k in await _keys(session, ids[0], "finish")]
    assert sorted(paid) == sorted(games[:8])
    assert (await client.delete(f"/api/games/{games[0]}")).status_code == 204
    paid_after = [k.removeprefix("finish:") for k in await _keys(session, ids[0], "finish")]
    assert sorted(paid_after) == sorted(games[1:])


async def test_a_streak_runs_across_a_year_boundary(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    # Tuesdays of ISO weeks 2025-W52, 2026-W01 and 2026-W02, noon in Asia/Kolkata.
    tuesdays = [(2025, 12, 23), (2025, 12, 30), (2026, 1, 6)]
    for k, (y, m, d) in enumerate(tuesdays):
        await _put(client, ids, to_ms(datetime(y, m, d, 6, tzinfo=UTC)), rounds=2, seed=k)

    streaks = [(e.key, e.amount) for e in await _entries(session, ids[0]) if e.kind == "streak"]
    assert streaks == [("streak:2025-W52", 10), ("streak:2026-W01", 20), ("streak:2026-W02", 30)]


async def test_a_winning_spades_team_both_win(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_spades(monkeypatch)
    registry = await seat_registry(session)
    user = await make_user(session, email="sp@example.com", player_name="Scorer")
    await sign_in(client, user)
    payload = case("won on target")

    response = await client.put(f"/api/spades/games/{payload['syncId']}", json=payload)

    assert response.status_code == 200, response.text
    seated = [p["playerId"] for p in payload["players"]]
    winners = [pid for pid in seated if await _keys(session, pid, "win")]
    assert len(winners) == 2
    assert all(pid in registry for pid in winners)
    finish = [e for pid in seated for e in await _entries(session, pid) if e.kind == "finish"]
    assert {(e.amount, e.game) for e in finish} == {(30, "spades")}


async def test_closing_a_day_pays_its_awards(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    yesterday = now_utc() - timedelta(days=1)
    await _put(client, ids, to_ms(yesterday.replace(hour=6, minute=0)), rounds=8, seed=2)
    day = from_ms(to_ms(yesterday.replace(hour=6))).date()

    awards = await AwardsService(session).close_day(day, ProgressionGame.KACHUFUL)

    assert awards
    rows = await session.execute(select(Achievement).where(Achievement.period == str(day)))
    for row in rows.scalars():
        keys = await _keys(session, str(row.player_id), "achievement")
        assert (f"ach:{row.id}" in keys) == (
            achievement_sikke(ProgressionGame.KACHUFUL, row.code) > 0
        )


async def test_a_merge_brings_the_merged_players_sikke_along(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    game = await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=3)
    keep = await make_player(session, name="Kept")
    admin = await make_user(session, email="admin@example.com", role="admin", player_name=None)
    before = sum(e.amount for e in await _entries(session, ids[0]))

    await MergeService(session).merge(uuid.UUID(ids[0]), keep.id, admin)

    assert await _entries(session, ids[0]) == []
    kept = await _entries(session, str(keep.id))
    assert sum(e.amount for e in kept) == before
    assert f"finish:{game}" in {e.key for e in kept}
    assert (await session.get(Player, uuid.UUID(ids[0]))) is not None


async def test_recompute_sikke_rebuilds_the_ledger_and_its_dry_run_writes_nothing(
    client: httpx.AsyncClient, session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = await _table(client, session, prefix="Cli")
    await _put(client, ids, to_ms(now_utc()) - 3 * HOUR, rounds=6, seed=5)
    await _put(client, ids, to_ms(now_utc()) - 2 * HOUR, rounds=6, seed=6)
    built = await _snapshot(session)
    totals = {pid: sum(e.amount for e in await _entries(session, pid)) for pid in ids}
    await session.execute(delete(SikkeEntry))

    assert await recompute_sikke.run(argparse.Namespace(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert await _snapshot(session) == []
    assert await recompute_sikke.run(argparse.Namespace(dry_run=False)) == 0
    rebuilt = await _snapshot(session)
    assert await recompute_sikke.run(argparse.Namespace(dry_run=False)) == 0

    # New ids, the same entries (player and key are unique, so the sort never reaches `data`).
    assert sorted(r[1:] for r in rebuilt) == sorted(r[1:] for r in built)
    assert await _snapshot(session) == rebuilt
    for k, pid in enumerate(ids):
        assert f"Cli{k}\t{totals[pid]}\t1\t1\t{totals[pid]}\n" in out
