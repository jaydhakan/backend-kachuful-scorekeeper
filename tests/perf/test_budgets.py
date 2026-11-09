"""Performance budgets, measured in-process against the load dataset.

Run with `make perf` after `make load`. Every response cache is cleared before each request, so
these are cold numbers; the report goes to var/perf-report.json.
"""

import asyncio
import json
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.bootstrap.app import create_app
from app.db.models import Game, Player, RoundScore
from app.db.session import dispose_engine, get_session_factory
from app.domain.time import now_utc, to_ms
from app.jobs.fake_games import MINUTE, make_payload
from app.live.hub import hub
from app.services.cache import response_cache
from tests.conftest import PERF_DATABASE_URL, WRITE_HEADERS

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(not PERF_DATABASE_URL, reason="needs PERF_DATABASE_URL (make perf)"),
]

RUNS = 40
BUDGETS_MS = {
    "GET /api/auth/me": 30,
    "GET /api/bootstrap": 80,
    "PUT /api/games/{id}": 120,
    "GET /api/leaderboard (worst range/metric)": 60,
    "GET /api/players/{id}": 80,
    "GET /api/players/{id}/stats": 80,
    "GET /api/players/{id}/games": 80,
    "GET /api/players/{id}/calendar": 80,
    "GET /api/players/{a}/vs/{b}": 80,
    "GET /api/games": 50,
    "304 Not Modified": 10,
    "live update (PUT to viewer)": 300,
}


def p95(samples: list[float]) -> float:
    """95th percentile in ms."""
    return statistics.quantiles(samples, n=20)[18] if len(samples) >= 20 else max(samples)


async def _measure(call: Callable[[], Awaitable[httpx.Response]], runs: int = RUNS) -> float:
    samples = []
    for _ in range(runs):
        response_cache.clear()
        started = time.perf_counter()
        response = await call()
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code in (200, 201, 304), response.text[:300]
    return p95(samples)


async def _dataset() -> dict[str, Any]:
    async with get_session_factory()() as session:
        games = (await session.execute(select(func.count()).select_from(Game))).scalar_one()
        rounds = (await session.execute(select(func.count()).select_from(RoundScore))).scalar_one()
        players = list(
            (await session.execute(select(Player).order_by(Player.display_name))).scalars()
        )
    return {"games": games, "roundScores": rounds, "players": players}


async def test_every_endpoint_is_within_its_budget() -> None:
    data = await _dataset()
    assert data["games"] >= 1000, "load the dataset first: make load"
    players: list[Player] = data["players"]
    a, b = players[0].id, players[1].id
    now = to_ms(now_utc())
    day = 86_400_000
    results: dict[str, float] = {}
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=WRITE_HEADERS
    ) as c:
        login = await c.post(
            "/api/auth/login", json={"email": "load0@kachuful.test", "password": "load-demo"}
        )
        assert login.status_code == 200, login.text
        results["GET /api/auth/me"] = await _measure(lambda: c.get("/api/auth/me"))
        results["GET /api/bootstrap"] = await _measure(lambda: c.get("/api/bootstrap"))

        worst = 0.0
        for metric in (
            "avgPerRound",
            "points",
            "wins",
            "winRate",
            "accuracy",
            "bestRound",
            "longestHot",
            "games",
            "rounds",
        ):
            for start in (now - day, now - 7 * day, now - 30 * day, 0):
                params = {"metric": metric, "from": start, "to": now + day}
                worst = max(
                    worst, await _measure(lambda p=params: c.get("/api/leaderboard", params=p), 10)
                )
        results["GET /api/leaderboard (worst range/metric)"] = worst

        results["GET /api/players/{id}"] = await _measure(lambda: c.get(f"/api/players/{a}"))
        results["GET /api/players/{id}/stats"] = await _measure(
            lambda: c.get(f"/api/players/{a}/stats")
        )
        results["GET /api/players/{id}/games"] = await _measure(
            lambda: c.get(f"/api/players/{a}/games")
        )
        year = now_utc().year
        results["GET /api/players/{id}/calendar"] = await _measure(
            lambda: c.get(f"/api/players/{a}/calendar", params={"year": year})
        )
        results["GET /api/players/{a}/vs/{b}"] = await _measure(
            lambda: c.get(f"/api/players/{a}/vs/{b}")
        )
        results["GET /api/games"] = await _measure(lambda: c.get("/api/games"))

        etag = (await c.get("/api/leaderboard")).headers["etag"]
        results["304 Not Modified"] = await _measure(
            lambda: c.get("/api/leaderboard", headers={"If-None-Match": etag})
        )

        uploads: list[float] = []
        made: list[str] = []
        for k in range(RUNS):
            table = [str(p.id) for p in players[k % 40 : k % 40 + 5]]
            payload = make_payload(table, start_ms=now - 90 * MINUTE, rounds=16, seed=10_000 + k)
            started = time.perf_counter()
            response = await c.put(f"/api/games/{payload['syncId']}", json=payload)
            uploads.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200, response.text
            made.append(payload["syncId"])
        results["PUT /api/games/{id}"] = p95(uploads)

        results["live update (PUT to viewer)"] = await _live_latency(c, players)
        for game_id in made:
            await c.delete(f"/api/games/{game_id}")
    await hub.close()
    await dispose_engine()

    report = {
        "dataset": {
            "games": data["games"],
            "roundScores": data["roundScores"],
            "players": len(players),
        },
        "p95_ms": {k: round(v, 1) for k, v in results.items()},
        "budget_ms": BUDGETS_MS,
    }
    Path("var").mkdir(exist_ok=True)
    Path("var/perf-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))  # noqa: T201 - the perf report is the point of `make perf`
    over = {k: v for k, v in results.items() if v > BUDGETS_MS[k]}
    assert not over, f"over budget: {over}"


async def _live_latency(c: httpx.AsyncClient, players: list[Player]) -> float:
    table = [str(p.id) for p in players[:4]]
    payload = make_payload(table, start_ms=to_ms(now_utc()) - 60 * MINUTE, rounds=3, finished=False)
    await c.put(f"/api/games/{payload['syncId']}", json=payload)
    token = (await c.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})).json()[
        "token"
    ]
    queue = await hub.subscribe(uuid.UUID(payload["syncId"]), token)
    samples = []
    for rev in range(2, 12):
        payload = {**payload, "rev": rev, "ui": {"view": "round", "bidIdx": rev}}
        started = time.perf_counter()
        await c.put(f"/api/games/{payload['syncId']}", json=payload)
        async with asyncio.timeout(5):
            while (await queue.get()).rev != rev:
                pass
        samples.append((time.perf_counter() - started) * 1000)
    await c.delete(f"/api/games/{payload['syncId']}")
    return max(samples)
