"""`?game=` on achievements, activity, daily awards and seasons; ladders; win-rate seasons; the
close jobs per game; and `recompute-achievements`."""

import argparse
import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, ClosedPeriod, User
from app.domain.enums import ProgressionGame
from app.domain.progression import Earned
from app.jobs import close_day, close_season, recompute_achievements
from app.repositories.achievements import achievements_repository
from app.services.awards import AwardsService, today
from tests.factories import make_player, make_user, sign_in
from tests.ishaara_builder import service_game, service_table
from tests.kt_builder import fixture_payload
from tests.kt_builder import freeze_clock as freeze_kt
from tests.progression_fake import AT, swap_in
from tests.spades_builder import finished_state, seat_registry, store_table

KT = ProgressionGame.KAALI_TEERI
SPADES = ProgressionGame.SPADES
ISHAARA = ProgressionGame.ISHAARA
KT_MONTH = "2026-11"  # kt-4p finished on 2026-11-02


async def _viewer(client: httpx.AsyncClient, session: AsyncSession) -> User:
    user = await make_user(session, email="viewer@example.com", player_name="Viewer")
    await sign_in(client, user)
    return user


async def _kt_game(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, list[str]]:
    """kt-4p uploaded by the signed-in viewer: (game id, its players)."""
    freeze_kt(monkeypatch)
    payload, registry = await fixture_payload(session, "kt-4p")
    response = await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    return payload["syncId"], sorted(str(p.id) for p in registry.values())


def _invalid_game(response: httpx.Response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid" and "game" in response.json()["fields"]


async def test_achievements_list_one_game_and_default_to_kachuful(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _viewer(client, session)
    player = await make_player(session, name="Both")
    for game in (ProgressionGame.KACHUFUL, SPADES):
        earned = Earned("first_win", str(player.id), None, None, None, AT)
        await achievements_repository.insert(session, [earned], game=game)
    params = {"playerId": str(player.id)}

    default = (await client.get("/api/achievements", params=params)).json()["items"]
    spades = (await client.get("/api/achievements", params=params | {"game": "spades"})).json()
    bad = await client.get("/api/achievements", params=params | {"game": "chess"})

    assert [(a["code"], a["gameKind"]) for a in default] == [("first_win", "kachuful")]
    assert [(a["code"], a["gameKind"]) for a in spades["items"]] == [("first_win", "spades")]
    _invalid_game(bad)


async def test_the_feed_filters_by_game_and_shows_each_kinds_summary(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = await _viewer(client, session)
    kt_id, _ = await _kt_game(client, session, monkeypatch)
    await seat_registry(session)
    table = await store_table(session, viewer, finished_state("spades-classic"))
    ishaara_id, _ = await service_game(session, await service_table(session))
    for game_id, game in ((table.id, SPADES), (ishaara_id, ISHAARA)):
        await achievements_repository.ensure_game_finished(session, game_id, game=game)

    every = (await client.get("/api/activity", params={"game": "all"})).json()["items"]
    kt = (await client.get("/api/activity", params={"game": "kaali-teeri"})).json()["items"]
    kachuful = (await client.get("/api/activity")).json()["items"]
    bad = await client.get("/api/activity", params={"game": "chess"})

    finished = {i["gameKind"]: i["game"] for i in every if i["kind"] == "game_finished"}
    assert set(finished) == {"kaali-teeri", "spades", "ishaara"}
    assert finished["kaali-teeri"]["id"] == kt_id
    assert finished["spades"]["kind"] == "spades-online" and finished["spades"]["isHost"]
    assert finished["ishaara"]["id"] == str(ishaara_id) and "myTeam" in finished["ishaara"]
    # The game's own achievements are in its feed too.
    assert {i["gameKind"] for i in kt} == {"kaali-teeri"}
    assert [i["game"]["id"] for i in kt if i["kind"] == "game_finished"] == [kt_id]
    assert all(i["gameKind"] == "kachuful" for i in kachuful)
    _invalid_game(bad)


async def test_ladders_count_each_games_own_ladders(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    viewer = await _viewer(client, session)
    params = {"playerId": str(viewer.player_id)}

    kachuful = await client.get("/api/achievements/ladders", params=params)
    spades = await client.get("/api/achievements/ladders", params=params | {"game": "spades"})
    unknown = await client.get("/api/achievements/ladders", params={"playerId": str(uuid.uuid4())})
    bad = await client.get("/api/achievements/ladders", params=params | {"game": "chess"})

    assert kachuful.json() == {
        "gameKind": "kachuful",
        "playerId": str(viewer.player_id),
        "counts": dict.fromkeys(
            ("hot", "cold", "wins", "games", "rounds", "made", "points", "crew"), 0
        ),
    }
    assert spades.json()["gameKind"] == "spades"
    assert set(spades.json()["counts"]) == {"wins", "games", "hands", "made", "nils", "crew"}
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"
    _invalid_game(bad)


async def test_other_games_seasons_rank_win_rate_and_close_on_their_own(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _viewer(client, session)
    _, players = await _kt_game(client, session, monkeypatch)
    path = f"/api/seasons/{KT_MONTH}"

    live = (await client.get(path, params={"game": "kaali-teeri"})).json()
    listing = (await client.get("/api/seasons", params={"game": "kaali-teeri"})).json()
    await AwardsService(session).close_season(KT_MONTH, KT)
    closed = await client.get(path, params={"game": "kaali-teeri"})
    kachuful = (await client.get(path)).json()

    assert live["gameKind"] == "kaali-teeri" and live["minGames"] == 8 and not live["final"]
    assert sorted(r["player"]["id"] for r in live["standings"]) == players
    assert all(r["games"] == 1 and r["rank"] is None for r in live["standings"])
    assert {r["wins"] for r in live["standings"]} <= {0, 1}
    assert listing[-1] == {"season": KT_MONTH, "champion": None}
    assert closed.json()["final"] is True and "immutable" in closed.headers["cache-control"]
    assert closed.json()["standings"] == live["standings"]
    assert "gameKind" not in kachuful and kachuful["final"] is False
    feed = select(Activity.game).where(Activity.kind == "season_closed")
    assert list((await session.execute(feed)).scalars()) == ["kaali-teeri"]


async def test_daily_awards_are_per_game_and_close_per_game(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = await _viewer(client, session)
    fake = swap_in(monkeypatch, SPADES)
    fake.players = [str(viewer.player_id)]
    day = (today() - timedelta(days=1)).isoformat()

    live = (await client.get("/api/awards/daily", params={"date": day, "game": "spades"})).json()
    await AwardsService(session).close_day(date.fromisoformat(day), SPADES)
    spades = await client.get("/api/awards/daily", params={"date": day, "game": "spades"})
    kachuful = (await client.get("/api/awards/daily", params={"date": day})).json()

    assert [a["code"] for a in live["awards"]] == ["t_day"] and live["final"] is False
    assert spades.json()["final"] is True and [a["code"] for a in spades.json()["awards"]] == [
        "t_day"
    ]
    assert kachuful["final"] is False and kachuful["awards"] == []
    stored = select(Achievement.game).where(Achievement.code == "t_day")
    assert list((await session.execute(stored)).scalars()) == ["spades"]


async def test_close_jobs_close_every_game_or_one_and_backfill_with_since(
    session: AsyncSession,
) -> None:
    since = (today() - timedelta(days=3)).isoformat()

    days = await close_day.run(argparse.Namespace(date=None, since=since, game="ishaara"))
    again = await close_day.run(argparse.Namespace(date=None, since=since, game=None))
    month = await close_season.run(argparse.Namespace(month="2026-09", since=None, game=None))
    future = await close_day.run(
        argparse.Namespace(date=today().isoformat(), since=None, game=None)
    )

    assert (days, again, month, future) == (0, 0, 0, 2)
    closed = select(ClosedPeriod.game, ClosedPeriod.kind, func.count()).group_by(
        ClosedPeriod.game, ClosedPeriod.kind
    )
    counts = {(g, k): n for g, k, n in await session.execute(closed)}
    assert counts == {
        **{(g.value, "day"): 3 for g in ProgressionGame},
        **{(g.value, "season"): 1 for g in ProgressionGame},
    }


async def test_recompute_rebuilds_a_games_rows_once_and_dates_their_feed_items(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _viewer(client, session)
    fake = swap_in(monkeypatch, KT)
    game_id, players = await _kt_game(client, session, monkeypatch)
    fake.players = players[:1]
    args = argparse.Namespace(game="kaali-teeri")

    first = await recompute_achievements.run(args)
    rows = await session.execute(
        select(Achievement.code, func.count())
        .where(Achievement.game == "kaali-teeri")
        .group_by(Achievement.code)
    )
    counts = dict(rows.all())
    await recompute_achievements.run(args)
    again = await session.execute(
        select(func.count()).select_from(Achievement).where(Achievement.game == "kaali-teeri")
    )

    assert first == 0
    assert counts == {"t_moment": 1, "t_career": 4}
    assert again.scalar_one() == 5
    moment = (
        select(Activity.created_at, Achievement.earned_at)
        .join(Achievement, Achievement.id == Activity.achievement_id)
        .where(Achievement.code == "t_moment")
    )
    assert [made == earned for made, earned in await session.execute(moment)] == [True]
    assert uuid.UUID(game_id) in fake.games


async def test_the_every_game_feed_reads_a_fixed_number_of_queries(
    client: httpx.AsyncClient, session: AsyncSession, queries: list[str]
) -> None:
    viewer = await _viewer(client, session)
    await seat_registry(session)
    table = await store_table(session, viewer, finished_state("spades-classic"))
    await achievements_repository.ensure_game_finished(session, table.id, game=SPADES)
    queries.clear()
    await client.get("/api/activity", params={"game": "all"})
    with_one = len(queries)
    for _ in range(3):
        more = await store_table(session, viewer, finished_state("spades-classic"))
        await achievements_repository.ensure_game_finished(session, more.id, game=SPADES)
    queries.clear()
    page = await client.get("/api/activity", params={"game": "all"})

    assert len(page.json()["items"]) == 4
    assert len(queries) == with_one
