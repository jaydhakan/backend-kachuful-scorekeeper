"""Daily awards and seasons through SQL equal the app's; close jobs freeze them."""

import argparse
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity
from app.jobs import close_day, close_season
from app.services.awards import AwardsService
from tests.domain.golden import camel, load
from tests.factories import make_user, sign_in
from tests.services.test_boards_parity import load_set

EXPECT = load("set-month")["expect"]


def _app_awards(awards: list[Any], letter_of: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for award in awards:
        row = {k: v for k, v in camel(award).items() if v is not None}
        row["playerId"] = letter_of[row["playerId"]]
        out.append(row)
    return out


@pytest.mark.parametrize("case", EXPECT["daily"], ids=[d["date"] for d in EXPECT["daily"]])
async def test_sql_daily_awards_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, case: dict[str, Any]
) -> None:
    letter_of = await load_set(session, monkeypatch)

    awards = await AwardsService(session).compute_daily(date.fromisoformat(case["date"]))

    assert _app_awards(awards, letter_of) == case["awards"]


@pytest.mark.parametrize("case", EXPECT["season"], ids=[s["season"] for s in EXPECT["season"]])
async def test_sql_seasons_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, case: dict[str, Any]
) -> None:
    letter_of = await load_set(session, monkeypatch)

    board, awards = await AwardsService(session).compute_season(case["season"])

    rows = [{**r, "playerId": letter_of[r["playerId"]]} for r in camel(board.rows)]
    assert rows == case["standings"]
    assert _app_awards(awards, letter_of) == case["awards"]


async def test_close_day_freezes_awards_once_and_serves_them_forever(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    await load_set(session, monkeypatch)
    viewer = await make_user(session, email="v@example.com", player_name="Viewer")
    await sign_in(client, viewer)

    first = await close_day.run(argparse.Namespace(date="2026-10-10"))
    again = await close_day.run(argparse.Namespace(date="2026-10-10"))
    response = await client.get("/api/awards/daily", params={"date": "2026-10-10"})

    assert (first, again) == (0, 0)
    stored = await session.execute(
        select(func.count()).select_from(Achievement).where(Achievement.period == "2026-10-10")
    )
    expected = next(d for d in EXPECT["daily"] if d["date"] == "2026-10-10")["awards"]
    assert stored.scalar_one() == len(expected)
    body = response.json()
    assert body["final"] is True
    assert [a["code"] for a in body["awards"]] == [a["code"] for a in expected]
    assert "immutable" in response.headers["cache-control"]
    revalidated = await client.get(
        "/api/awards/daily",
        params={"date": "2026-10-10"},
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert revalidated.status_code == 304


async def test_close_day_refuses_today(session: AsyncSession) -> None:
    from app.services.awards import today

    assert await close_day.run(argparse.Namespace(date=today().isoformat())) == 2


async def test_close_season_freezes_last_months_awards_and_posts_to_the_feed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    await load_set(session, monkeypatch)
    viewer = await make_user(session, email="v@example.com", player_name="Viewer")
    await sign_in(client, viewer)

    code = await close_season.run(argparse.Namespace(month="2026-09"))
    response = await client.get("/api/seasons/2026-09")
    listing = await client.get("/api/seasons")

    assert code == 0
    assert response.json()["final"] is True
    assert response.json()["season"] == "2026-09"
    feed = await session.execute(
        select(func.count()).select_from(Activity).where(Activity.kind == "season_closed")
    )
    assert feed.scalar_one() == 1
    seasons = [s["season"] for s in listing.json()]
    assert seasons[0] >= "2026-10" and "2026-09" in seasons
    assert seasons == sorted(seasons, reverse=True)


async def test_live_season_and_daily_are_provisional(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    await load_set(session, monkeypatch)
    viewer = await make_user(session, email="v@example.com", player_name="Viewer")
    await sign_in(client, viewer)

    season = await client.get("/api/seasons/2026-10")
    daily = await client.get("/api/awards/daily", params={"date": "2026-10-11"})

    assert season.json()["final"] is False
    assert len(season.json()["standings"]) > 0
    assert daily.json()["final"] is False
    assert daily.headers["cache-control"] == "private, no-cache"


@pytest.mark.parametrize(
    ("path", "params"),
    [("/api/awards/daily", {"date": "31-10-2026"}), ("/api/seasons/2026-13", {})],
)
async def test_bad_dates_return_invalid(
    session: AsyncSession, client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> None:
    await sign_in(client, await make_user(session))

    response = await client.get(path, params=params)

    assert response.status_code == 422


async def test_wrapped_summarises_a_players_year(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    letter_of = await load_set(session, monkeypatch)
    ana = next(pid for pid, letter in letter_of.items() if letter == "ana")
    await sign_in(client, await make_user(session, email="v@example.com", player_name="Viewer"))

    response = await client.get(f"/api/wrapped/2026/{ana}")

    body = response.json()
    assert response.status_code == 200
    assert body["year"] == 2026
    assert body["totals"]["rounds"] > 0
    assert body["bestMonth"]["month"] in {"2026-09", "2026-10"}
    assert len(body["topDays"]) <= 3
    assert body["biggestRound"]["points"] > 0
    assert body["partner"]["games"] > 0
    assert body["achievements"] > 0
    assert (
        await client.get(f"/api/wrapped/2026/{'0' * 8}-0000-4000-8000-{'9' * 12}")
    ).status_code == 404
