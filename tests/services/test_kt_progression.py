"""Kaali Teeri's real rules through the engine: an upload stores and returns what it earned,
recomputes keep rows as they are, and ladders, daily awards and seasons read the stored rows."""

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.enums import ProgressionGame
from app.domain.time import month_range_ms
from app.services.achievements import AchievementsEngine
from app.services.progression.registry import KAALI_TEERI
from app.settings import settings
from tests.factories import make_user, sign_in
from tests.kt_builder import fixture_payload, freeze_clock

KT = ProgressionGame.KAALI_TEERI


async def _stored(session: AsyncSession, game_id: str) -> list[tuple[str, str, str, int | None]]:
    stmt = select(
        Achievement.game, Achievement.code, Achievement.player_id, Achievement.round_index
    )
    rows = await session.execute(stmt.where(Achievement.game_id == uuid.UUID(game_id)))
    return sorted((g, c, str(p), r) for g, c, p, r in rows)


async def _upload(
    client: httpx.AsyncClient, session: AsyncSession, name: str
) -> tuple[httpx.Response, dict[str, str], str]:
    user = await make_user(session, email="kt-scorer@example.com", player_name="Scorer")
    await sign_in(client, user)
    payload, registry = await fixture_payload(session, name)
    response = await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=payload)
    return response, {k: str(p.id) for k, p in registry.items()}, payload["syncId"]


async def test_an_upload_stores_and_returns_the_hands_moments_and_the_finish(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_clock(monkeypatch)

    # Six players; the bidder plays one called card, dee the other, and they take all 250.
    response, ids, game_id = await _upload(client, session, "kt-sweep-bidders")

    assert response.status_code == 200, response.text
    stored = await _stored(session, game_id)
    side = sorted([ids["ana"], ids["dee"]])
    assert [(c, p, r) for g, c, p, r in stored if c == "clean_sweep"] == [
        ("clean_sweep", p, 0) for p in side
    ]
    assert [p for _, c, p, _ in stored if c == "winner"] == side
    assert {g for g, *_ in stored} == {"kaali-teeri"}
    earned = response.json()["earned"]
    assert {e["gameKind"] for e in earned} == {"kaali-teeri"}
    assert sorted((e["code"], e["playerId"]) for e in earned if e["code"] == "clean_sweep") == [
        ("clean_sweep", p) for p in side
    ]
    # Their first win, earned with the game.
    assert sorted(e["playerId"] for e in earned if e["code"] == "first_win") == side


async def test_recomputing_adds_nothing_and_keeps_when_rows_were_earned(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_clock(monkeypatch)
    _, ids, game_id = await _upload(client, session, "kt-bid-250")
    before = (await session.execute(select(Achievement.id, Achievement.earned_at))).all()

    engine = AchievementsEngine(session)
    added = await engine.recompute_game(KAALI_TEERI, uuid.UUID(game_id))
    added += await engine.recompute_careers(KAALI_TEERI, {uuid.UUID(p) for p in ids.values()})

    assert added == []
    after = (await session.execute(select(Achievement.id, Achievement.earned_at))).all()
    assert sorted(after) == sorted(before)
    codes = {c for _, c, _, _ in await _stored(session, game_id)}
    # A 250 bid that takes everything is All In, High Roller and a clean sweep.
    assert {"all_in", "clean_sweep", "high_roller"} <= codes


async def test_ladders_daily_awards_and_seasons_read_the_stored_rows(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_clock(monkeypatch)
    _, ids, _ = await _upload(client, session, "kt-to-1000")

    ladders = await KAALI_TEERI.ladder_counts(session, uuid.UUID(ids["ana"]))
    first = await KAALI_TEERI.first_played(session)
    assert first is not None
    awards = await KAALI_TEERI.daily_awards(session, first)
    month = first.isoformat()[:7]
    tallies = await KAALI_TEERI.month_tallies(session, *month_range_ms(month, settings.tz))

    assert ladders == {
        "wins": 1,
        "games": 1,
        "hands": 6,
        "made": 5,
        "partner": 0,
        "streak": 5,
        "crew": 3,
    }
    assert ("day_star", ids["ana"]) in {(a.code, a.player_id) for a in awards}
    assert ("day_bid", ids["ana"]) in {(a.code, a.player_id) for a in awards}
    assert sorted(t.player_id for t in tallies) == sorted(ids.values())
    # One game is far short of a season's eight: nobody ranked, no podium.
    assert await KAALI_TEERI.season_awards(session, month, [], []) == []


def test_the_registry_wires_the_real_codes() -> None:
    assert len(KAALI_TEERI.game_codes) == 21
    assert len(KAALI_TEERI.career_codes) == 23
    assert KAALI_TEERI.min_games == 8
