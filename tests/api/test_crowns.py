"""`GET /api/crowns`, `GET /api/crowns/reigns`, and the Kachuful PUT's `status` and stream frame."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CrownReign
from app.domain.time import now_utc, to_ms
from app.jobs.fake_games import MINUTE
from app.services import games as games_service
from tests.factories import make_player, make_user, sign_in
from tests.placed_games import backdate_taj, placed_payload, taj_payload

HOUR = 60 * MINUTE
KINDS = ["medal1", "medal2", "medal3", "sitara", "legend"]


async def _table(client: httpx.AsyncClient, session: AsyncSession) -> list[str]:
    user = await make_user(session, email="k@example.com", player_name="Keeper")
    await sign_in(client, user)
    return [str((await make_player(session, name=f"K{k}")).id) for k in range(3)]


async def _put(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.put(f"/api/games/{payload['syncId']}", json=payload)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _ids(players: list[dict[str, Any]]) -> list[str]:
    return [p["id"] for p in players]


async def test_crowns_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/crowns")).status_code == 401
    assert (await client.get("/api/crowns/reigns")).status_code == 401


async def test_crowns_list_all_five_titles_with_nobody_at_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _table(client, session)

    body = (await client.get("/api/crowns")).json()

    assert body == {
        "items": [
            {"kind": k, "holders": [], "since": None, "sinceGame": None, "defences": 0}
            for k in KINDS
        ]
    }


async def test_the_put_reports_its_bounty(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = await _table(client, session)
    sent: list[tuple[object, str, dict[str, Any]]] = []

    async def record(_: AsyncSession, user_id: object, event: str, data: dict[str, Any]) -> None:
        sent.append((user_id, event, data))

    monkeypatch.setattr(games_service, "notify_user", record)
    now = to_ms(now_utc())
    await _put(client, taj_payload([a, b, c], start_ms=now - 5 * HOUR))
    await backdate_taj(session, now - 5 * HOUR)
    sent.clear()
    second = placed_payload([b, a, c], start_ms=now - 2 * HOUR)

    body = await _put(client, second)

    assert [
        (s["holder"]["id"], s["winner"]["id"], s["amount"]) for s in body["status"]["bounties"]
    ] == [(a, b, 40)]
    # None of the seats has an account, so nobody is told on the stream.
    assert sent == []


async def test_the_status_frame_goes_to_seated_accounts(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scorer = await make_user(session, email="s@example.com", player_name="Seated")
    await sign_in(client, scorer)
    assert scorer.player_id is not None
    others = [str((await make_player(session, name=f"O{k}")).id) for k in range(2)]
    sent: list[tuple[object, str, dict[str, Any]]] = []

    async def record(_: AsyncSession, user_id: object, event: str, data: dict[str, Any]) -> None:
        sent.append((user_id, event, data))

    monkeypatch.setattr(games_service, "notify_user", record)
    order = [str(scorer.player_id), *others]
    now = to_ms(now_utc())
    await _put(client, placed_payload(order, start_ms=now - 30 * MINUTE))
    assert sent == []
    # The second game of the day crowns the Sitara, so this write has a change to tell.
    game = placed_payload(order, start_ms=now - 20 * MINUTE)

    body = await _put(client, game)

    assert [(u, e) for u, e, _ in sent] == [(scorer.id, "status")]
    frame = sent[0][2]
    assert frame["type"] == "status" and frame["game"] == "kachuful"
    assert frame["gameId"] == game["syncId"]
    assert frame["changes"] == body["status"]["changes"]
    assert frame["bounties"] == body["status"]["bounties"] == []


async def test_an_unchanged_put_has_an_empty_status(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ids = await _table(client, session)
    game = placed_payload(ids, start_ms=to_ms(now_utc()) - 2 * HOUR)
    await _put(client, game)

    again = await _put(client, game)

    assert again["status"] == {"changes": [], "bounties": []}


async def test_crowns_show_the_holder_since_the_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, c = await _table(client, session)
    now = to_ms(now_utc())
    await _put(client, placed_payload([a, b, c], start_ms=now - 30 * MINUTE))
    game = placed_payload([a, b, c], start_ms=now - 20 * MINUTE)
    await _put(client, game)

    items = {i["kind"]: i for i in (await client.get("/api/crowns")).json()["items"]}

    sitara = items["sitara"]
    assert _ids(sitara["holders"]) == [a]
    assert sitara["since"] >= now and sitara["sinceGame"] == game["syncId"]


async def test_other_games_have_no_holders_yet(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _table(client, session)

    body = (await client.get("/api/crowns", params={"game": "spades"})).json()

    assert [i["holders"] for i in body["items"]] == [[]] * 5
    assert (await client.get("/api/crowns", params={"game": "chess"})).status_code == 422


async def _three_reigns(client: httpx.AsyncClient, session: AsyncSession) -> list[str]:
    """Closed reigns of a, then b, then a again (medal1), and one of c (sitara), oldest first."""
    a, b, c = await _table(client, session)
    now = now_utc()
    held = [("medal1", a), ("medal1", b), ("sitara", c), ("medal1", a)]
    for k, (kind, pid) in enumerate(held):
        session.add(
            CrownReign(
                game="kachuful",
                kind=kind,
                holders=[uuid.UUID(pid)],
                since=now - timedelta(hours=10 - k),
                until=now - timedelta(hours=9 - k),
            )
        )
    await session.flush()
    return [a, b, c]


async def test_reigns_list_closed_reigns_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    a, b, _ = await _three_reigns(client, session)

    taj = {"kind": "medal1"}
    recent = (await client.get("/api/crowns/reigns", params=taj)).json()
    by_defences = (
        await client.get("/api/crowns/reigns", params={**taj, "sort": "defences"})
    ).json()
    everything = (await client.get("/api/crowns/reigns")).json()

    assert [(_ids(r["holders"]), r["defences"]) for r in recent["items"]] == [
        ([a], 0),
        ([b], 0),
        ([a], 0),
    ]
    assert all(r["kind"] == "medal1" and r["since"] < r["until"] for r in recent["items"])
    # No title counts defences, so that sort falls back to the newest end.
    assert by_defences["items"] == recent["items"]
    assert recent["nextCursor"] is None
    assert [r["kind"] for r in everything["items"]] == ["medal1", "sitara", "medal1", "medal1"]


async def test_reigns_page_with_a_cursor(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await _three_reigns(client, session)
    for sort in ("recent", "defences"):
        everything = (await client.get("/api/crowns/reigns", params={"sort": sort})).json()
        first = (await client.get("/api/crowns/reigns", params={"sort": sort, "limit": 2})).json()
        rest = (
            await client.get(
                "/api/crowns/reigns",
                params={"sort": sort, "limit": 2, "cursor": first["nextCursor"]},
            )
        ).json()

        assert first["items"] + rest["items"] == everything["items"]
        assert rest["nextCursor"] is None


async def test_reigns_reject_a_bad_cursor_sort_or_kind(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _table(client, session)
    for params in ({"cursor": "nope"}, {"sort": "oldest"}, {"kind": "crown"}, {"kind": "kursi"}):
        response = await client.get("/api/crowns/reigns", params=params)
        assert response.status_code == 422, params
        assert response.json()["code"] == "invalid"
