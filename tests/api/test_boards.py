import copy
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, Player, User
from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload

HOUR = 60 * MINUTE


async def _setup(
    client: httpx.AsyncClient, session: AsyncSession, games: int = 1
) -> tuple[User, list[Player], list[dict[str, Any]]]:
    user = await make_user(session, player_name="Scorer")
    await sign_in(client, user)
    players = [await make_player(session, name=f"B{k}") for k in range(3)]
    payloads = []
    for k in range(games):
        start = to_ms(now_utc()) - (k + 2) * HOUR
        payload = make_payload([str(p.id) for p in players], start_ms=start, seed=k, rounds=6)
        response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
        assert response.status_code == 200, response.text
        payloads.append(payload)
    return user, players, payloads


@pytest.mark.parametrize(
    "path",
    [
        "/api/leaderboard",
        "/api/bootstrap",
        "/api/achievements",
        "/api/activity",
        f"/api/players/{uuid.uuid4()}",
        f"/api/players/{uuid.uuid4()}/stats",
        f"/api/players/{uuid.uuid4()}/games",
        f"/api/players/{uuid.uuid4()}/calendar?year=2026",
        f"/api/players/{uuid.uuid4()}/vs/{uuid.uuid4()}",
    ],
)
async def test_board_routes_need_a_session(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 401


async def test_leaderboard_ranks_players_with_summaries_and_etag(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session)

    response = await client.get("/api/leaderboard", params={"metric": "points"})

    assert response.status_code == 200
    body = response.json()
    assert body["minRounds"] == 50
    assert {r["player"]["id"] for r in body["rows"]} == {str(p.id) for p in players}
    assert all(r["qualified"] for r in body["rows"])
    assert body["rows"][0]["rank"] == 1
    assert body["rows"][0]["player"]["games"] == 1
    assert response.headers["etag"].startswith('"')
    assert response.headers["cache-control"] == "private, no-cache"


async def test_leaderboard_rate_metric_leaves_players_under_the_minimum_unranked(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _setup(client, session)

    response = await client.get("/api/leaderboard")

    rows = response.json()["rows"]
    assert all(r["rank"] is None and r["qualified"] is False for r in rows)
    custom = await client.get("/api/leaderboard", params={"minRounds": 5})
    assert all(r["qualified"] for r in custom.json()["rows"])


async def test_leaderboard_min_games_and_limit_trim_rows_but_keep_ranks(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _setup(client, session, games=3)
    await _setup_more(client, session, 1)
    full = (await client.get("/api/leaderboard", params={"metric": "rounds"})).json()["rows"]

    response = await client.get(
        "/api/leaderboard", params={"metric": "rounds", "minGames": 2, "limit": 2}
    )

    rows = response.json()["rows"]
    assert len(rows) == 2
    assert all(r["games"] >= 2 for r in rows)
    expected = [r for r in full if r["games"] >= 2][:2]
    assert [(r["player"]["id"], r["rank"]) for r in rows] == [
        (r["player"]["id"], r["rank"]) for r in expected
    ]
    none = await client.get("/api/leaderboard", params={"minGames": 99})
    assert none.json()["rows"] == []


async def test_leaderboard_answers_304_until_the_data_changes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, _, payloads = await _setup(client, session)
    first = await client.get("/api/leaderboard")
    etag = first.headers["etag"]

    same = await client.get("/api/leaderboard", headers={"If-None-Match": f"W/{etag}"})
    newer = copy.deepcopy(payloads[0])
    newer["rev"] = 2
    newer["ui"] = {"view": "summary", "bidIdx": 0}
    await client.put(f"/api/games/{newer['syncId']}", json=newer)
    changed = await client.get("/api/leaderboard", headers={"If-None-Match": etag})

    assert same.status_code == 304
    assert same.content == b""
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag


async def test_leaderboard_query_count_does_not_grow_with_data(
    client: httpx.AsyncClient, session: AsyncSession, queries: list[str]
) -> None:
    await _setup(client, session, games=1)
    queries.clear()
    await client.get("/api/leaderboard", params={"metric": "wins"})
    with_one = len(queries)
    await _setup_more(client, session, 4)
    queries.clear()
    await client.get("/api/leaderboard", params={"metric": "wins"})

    assert len(queries) == with_one


async def _setup_more(client: httpx.AsyncClient, session: AsyncSession, n: int) -> None:
    players = [await make_player(session, name=f"M{k}") for k in range(3)]
    for k in range(n):
        start = to_ms(now_utc()) - (k + 5) * HOUR
        payload = make_payload([str(p.id) for p in players], start_ms=start, seed=k + 10)
        await client.put(f"/api/games/{payload['syncId']}", json=payload)


@pytest.mark.parametrize(
    "params",
    [{"from": 10, "to": 10}, {"metric": "luck"}, {"from": -1}, {"limit": 0}, {"minGames": -1}],
)
async def test_leaderboard_with_a_bad_query_returns_invalid(
    client: httpx.AsyncClient, session: AsyncSession, params: dict[str, Any]
) -> None:
    await _setup(client, session)

    response = await client.get("/api/leaderboard", params=params)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid"


async def test_player_page_has_totals_achievements_and_rank(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session)

    response = await client.get(f"/api/players/{players[0].id}")

    assert response.status_code == 200
    body = response.json()
    assert body["player"]["id"] == str(players[0].id)
    assert body["totals"]["rounds"] == 6
    assert body["totals"]["games"] == 1
    assert body["totals"]["gamesEnded"] == 1
    assert sum(body["totals"]["places"].values()) == 1
    assert body["rank"] == {"global": None, "outOf": 0}
    assert isinstance(body["achievements"], list)


async def test_player_stats_cover_histograms_and_best_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, payloads = await _setup(client, session)

    response = await client.get(f"/api/players/{players[1].id}/stats")

    stats = response.json()
    assert stats["rounds"] == 6
    assert sum(v["total"] for v in stats["bidHistogram"].values()) == 6
    assert sum(v["total"] for v in stats["byCards"].values()) == 6
    assert sum(v["total"] for v in stats["byTrump"].values()) == 6
    assert stats["asDealer"]["rounds"] == 2
    assert stats["bestGame"]["id"] == payloads[0]["syncId"]
    assert 0 <= stats["accuracy"] <= 1


async def test_player_stats_for_an_empty_range_are_zero(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session)

    response = await client.get(
        f"/api/players/{players[0].id}/stats", params={"from": 0, "to": 1000}
    )

    assert response.json()["rounds"] == 0
    assert response.json()["bestRound"] is None
    assert response.json()["bestGame"] is None


async def test_unknown_player_pages_return_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _setup(client, session)
    unknown = uuid.uuid4()

    responses = [
        await client.get(f"/api/players/{unknown}"),
        await client.get(f"/api/players/{unknown}/stats"),
        await client.get(f"/api/players/{unknown}/games"),
        await client.get(f"/api/players/{unknown}/calendar", params={"year": 2026}),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]


async def test_player_games_are_newest_first_with_a_cursor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, payloads = await _setup(client, session, games=3)

    first = await client.get(f"/api/players/{players[0].id}/games", params={"limit": 2})
    second = await client.get(
        f"/api/players/{players[0].id}/games",
        params={"limit": 2, "cursor": first.json()["nextCursor"]},
    )

    ids = [g["id"] for g in first.json()["items"] + second.json()["items"]]
    assert ids == [p["syncId"] for p in payloads]
    assert second.json()["nextCursor"] is None


async def test_calendar_counts_rounds_per_day(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session)
    year = now_utc().year

    response = await client.get(f"/api/players/{players[0].id}/calendar", params={"year": year})

    assert sum(response.json()["days"].values()) == 6


async def test_head_to_head_compares_two_players_over_shared_games(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session, games=2)
    a, b = players[0], players[1]

    response = await client.get(f"/api/players/{a.id}/vs/{b.id}")

    body = response.json()
    assert body["games"] == 2
    assert body["aAhead"] + body["bAhead"] + body["ties"] == 2
    assert len(body["lastGames"]) == 2
    assert body["a"]["id"] == str(a.id)
    if body["biggestWin"] is not None:
        assert body["biggestWin"]["winner"] in {str(a.id), str(b.id)}


async def test_bootstrap_returns_everything_and_supports_304(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user, _, payloads = await _setup(client, session)

    response = await client.get("/api/bootstrap")
    again = await client.get("/api/bootstrap", headers={"If-None-Match": response.headers["etag"]})

    body = response.json()
    assert body["user"]["id"] == str(user.id)
    assert body["player"]["displayName"] == "Scorer"
    assert len(body["players"]) == 4
    assert [g["id"] for g in body["myGames"]] == [payloads[0]["syncId"]]
    assert body["myAchievements"] == []
    assert isinstance(body["dataVersion"], int)
    assert again.status_code == 304


async def test_achievements_list_pages_and_filters_by_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, players, _ = await _setup(client, session, games=2)

    everyone = await client.get("/api/achievements", params={"limit": 3})
    mine = await client.get("/api/achievements", params={"playerId": str(players[0].id)})
    more = await client.get(
        "/api/achievements", params={"limit": 3, "cursor": everyone.json()["nextCursor"]}
    )

    assert len(everyone.json()["items"]) == 3
    assert {a["playerId"] for a in mine.json()["items"]} == {str(players[0].id)}
    first_ids = {a["id"] for a in everyone.json()["items"]}
    assert not first_ids & {a["id"] for a in more.json()["items"]}


async def test_activity_feed_lists_finished_games_and_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _setup(client, session)

    response = await client.get("/api/activity")

    kinds = {item["kind"] for item in response.json()["items"]}
    assert {"game_finished", "achievement"} <= kinds
    finished = next(i for i in response.json()["items"] if i["kind"] == "game_finished")
    assert finished["game"]["status"] == "finished"


async def test_undoing_a_round_unearns_its_achievements_and_feed_items(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, _, payloads = await _setup(client, session)
    game_id = uuid.UUID(payloads[0]["syncId"])
    undone = copy.deepcopy(payloads[0])
    undone.update(rev=2, status="active", finishedAt=None, rounds=undone["rounds"][:1])

    response = await client.put(f"/api/games/{game_id}", json=undone)

    assert response.status_code == 200
    codes = (
        (await session.execute(select(Achievement.code).where(Achievement.game_id == game_id)))
        .scalars()
        .all()
    )
    assert "winner" not in codes
    finished = await session.execute(
        select(func.count()).select_from(Activity).where(Activity.kind == "game_finished")
    )
    assert finished.scalar_one() == 0


async def test_deleting_a_game_removes_its_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, _, payloads = await _setup(client, session)

    await client.delete(f"/api/games/{payloads[0]['syncId']}")

    left = await session.execute(select(func.count()).select_from(Achievement))
    assert left.scalar_one() == 0
