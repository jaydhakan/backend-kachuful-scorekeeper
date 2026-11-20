"""Crowns after Kachuful writes: the Kursi's reigns and feed items, bounties, and no-op recomputes."""

import argparse
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, Crown, CrownReign, SikkeEntry
from app.domain.time import day_of, from_ms, now_utc, to_ms
from app.jobs import recompute_crowns
from app.jobs.fake_games import MINUTE
from app.services.crowns import CrownsService
from app.services.sikke import SikkeService
from app.settings import settings
from tests.factories import make_player, make_user, sign_in
from tests.placed_games import placed_payload

HOUR = 60 * MINUTE


async def _table(client: httpx.AsyncClient, session: AsyncSession, n: int = 3) -> list[str]:
    user = await make_user(session, email="c@example.com", player_name="Crowner")
    await sign_in(client, user)
    return [str((await make_player(session, name=f"C{k}")).id) for k in range(n)]


async def _put(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _crowns(session: AsyncSession) -> dict[str, list[str]]:
    rows = (await session.execute(select(Crown))).scalars()
    return {r.kind: sorted(map(str, r.holders)) for r in rows}


async def _snapshot(session: AsyncSession) -> tuple[list[Any], ...]:
    crowns = [
        (c.kind, c.holders, c.since, c.since_game, c.defences)
        for c in (await session.execute(select(Crown).order_by(Crown.kind))).scalars()
    ]
    reigns = [
        (r.id, r.since, r.until, r.defences)
        for r in (await session.execute(select(CrownReign).order_by(CrownReign.id))).scalars()
    ]
    feed = list((await session.execute(select(Activity.id).order_by(Activity.id))).scalars())
    sikke = [
        (e.id, e.key, e.amount, e.earned_at)
        for e in (await session.execute(select(SikkeEntry).order_by(SikkeEntry.id))).scalars()
    ]
    return crowns, reigns, feed, sikke


async def _bounties(session: AsyncSession) -> list[tuple[str, str, str]]:
    stmt = select(SikkeEntry).where(SikkeEntry.kind == "bounty")
    rows = (await session.execute(stmt)).scalars()
    return sorted((str(e.player_id), e.key, str(e.game_id)) for e in rows)


def _week(ended_at: int) -> str:
    year, week, _ = day_of(ended_at, settings.tz).isocalendar()
    return f"{year}-W{week:02d}"


async def test_the_first_game_crowns_its_winner_with_a_feed_item_and_no_reign(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    game = placed_payload([a, b, c], start_ms=to_ms(now_utc()) - 2 * HOUR)
    await _put(client, game)

    assert (await _crowns(session))["kursi"] == [a]
    feed = (await session.execute(select(Activity).where(Activity.kind == "crown"))).scalars()
    kursi = [f for f in feed if f.data["crown"] == "kursi"]
    assert [(f.data["from"], f.data["to"], f.player_id) for f in kursi] == [([], [a], uuid.UUID(a))]
    assert kursi[0].game_id == uuid.UUID(game["syncId"])
    assert (await session.execute(select(CrownReign))).scalars().first() is None


async def test_a_take_closes_the_reign_and_pays_a_bounty_once_a_week(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    first = placed_payload([a, b, c], start_ms=now - 3 * HOUR)
    second = placed_payload([b, a, c], start_ms=now - 2 * HOUR)
    third = placed_payload([b, a, c], start_ms=now - HOUR)
    await _put(client, first)
    await _put(client, second)
    await _put(client, third)

    assert (await _crowns(session))["kursi"] == [b]
    reigns = list((await session.execute(select(CrownReign))).scalars())
    assert [(r.kind, list(map(str, r.holders)), r.until_game) for r in reigns] == [
        ("kursi", [a], uuid.UUID(second["syncId"]))
    ]
    # b beat the holder a in the second game; a third win over a that week pays nothing more.
    week = _week(second["finishedAt"])
    assert await _bounties(session) == [(b, f"bounty:{a}:{week}", second["syncId"])]


async def test_recomputing_without_a_change_writes_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 3 * HOUR))
    await _put(client, placed_payload([b, a, c], start_ms=now - 2 * HOUR))
    before = await _snapshot(session)
    crowns = CrownsService(session)

    for _ in range(2):
        chain = await crowns.chain()
        assert await crowns.recompute(chain, game_id=None) == []
        assert await SikkeService(session).recompute_kursi(chain) == []

    assert await _snapshot(session) == before


async def test_deleting_the_game_takes_the_bounty_and_the_reign_back(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 3 * HOUR))
    second = placed_payload([b, a, c], start_ms=now - 2 * HOUR)
    await _put(client, second)
    assert await _bounties(session) != []

    response = await client.delete(f"/api/games/{second['syncId']}")

    assert response.status_code == 204
    assert await _bounties(session) == []
    assert (await _crowns(session))["kursi"] == [a]
    assert (await session.execute(select(CrownReign))).scalars().first() is None


async def test_a_voided_bounty_goes_to_the_next_qualifying_game_that_week(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 4 * HOUR))
    second = placed_payload([b, a, c], start_ms=now - 3 * HOUR)
    await _put(client, second)
    # b takes the Kursi in `second`; with it gone, b's win over a in `third` pays instead.
    third = placed_payload([b, a, c], start_ms=now - 2 * HOUR)
    await _put(client, third)

    await client.delete(f"/api/games/{second['syncId']}")

    week = _week(third["finishedAt"])
    assert await _bounties(session) == [(b, f"bounty:{a}:{week}", third["syncId"])]


async def test_the_legend_needs_two_season_titles(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, _ = await _table(client, session)
    for month, pid in (("2026-08", a), ("2026-09", a), ("2026-10", b)):
        session.add(
            Achievement(
                game="kachuful",
                player_id=uuid.UUID(pid),
                code="season_champion",
                period=month,
                earned_at=from_ms(to_ms(now_utc()) - 30 * 24 * HOUR),
            )
        )
    await session.flush()
    crowns = CrownsService(session)

    changes = await crowns.recompute(await crowns.chain(), game_id=None)

    assert [(c.kind.value, c.to) for c in changes] == [("legend", (a,))]
    assert (await _crowns(session))["legend"] == [a]


async def test_the_sitara_needs_two_games_today(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 30 * MINUTE))
    assert (await _crowns(session)).get("sitara", []) == []

    await _put(client, placed_payload([a, b, c], start_ms=now - 20 * MINUTE))

    assert (await _crowns(session))["sitara"] == [a]


async def test_recompute_crowns_rebuilds_the_kursi_reigns_without_feed_items(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 3 * HOUR))
    await _put(client, placed_payload([b, a, c], start_ms=now - 2 * HOUR))
    before = await _snapshot(session)
    await session.execute(Crown.__table__.delete())
    await session.execute(CrownReign.__table__.delete())

    assert await recompute_crowns.run(argparse.Namespace(dry_run=False)) == 0

    crowns, reigns, feed, _ = await _snapshot(session)
    assert [(k, h, d) for k, h, _, _, d in crowns] == [(k, h, d) for k, h, _, _, d in before[0]]
    assert [(s, u, d) for _, s, u, d in reigns] == [(s, u, d) for _, s, u, d in before[1]]
    assert feed == before[2]
