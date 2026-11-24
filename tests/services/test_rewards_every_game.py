"""Crowns, bounties, medals, places and milestones for a game other than Kachuful (Kaali Teeri)."""

import uuid
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Activity, Crown, Game, KtGamePlayer, Milestone, SikkeEntry, User
from app.domain.enums import ProgressionGame
from app.domain.milestones import GROUP_GAME_MARKS
from app.domain.time import day_of, from_ms, now_utc, to_ms
from app.jobs.fake_games import MINUTE
from app.services import milestones as milestones_service
from app.services import rewards as rewards_service
from app.services.crowns import CrownsService
from app.services.milestones import MilestonesService
from app.services.rewards import reward_game
from app.settings import settings
from tests.factories import make_player, make_user, sign_in

KT = ProgressionGame.KAALI_TEERI
HOUR = 60 * MINUTE


async def _kt_game(
    session: AsyncSession, owner: User, places: list[tuple[str, int]], *, ended_ms: int
) -> uuid.UUID:
    """A finished Kaali Teeri game as its derived rows: (player, place) per seat; 1st wins."""
    game_id = uuid.uuid4()
    ended = from_ms(ended_ms)
    session.add(
        Game(
            id=game_id,
            kind="kaali-teeri",
            owner_id=owner.id,
            rev=1,
            payload={},
            payload_hash="x",
            status="finished",
            player_count=len(places),
            round_count=1,
            summary={},
            created_at=from_ms(ended_ms - 30 * MINUTE),
            client_updated_at=ended,
            finished_at=ended,
            ended_at=ended,
        )
    )
    await session.flush()
    for seat, (pid, place) in enumerate(places):
        session.add(_seat(game_id, pid, seat, place, ended))
    await session.flush()
    return game_id


def _seat(game_id: uuid.UUID, pid: str, seat: int, place: int, ended: datetime) -> KtGamePlayer:
    return KtGamePlayer(
        game_id=game_id,
        player_id=uuid.UUID(pid),
        seat=seat,
        points=100 - place,
        place=place,
        won=place == 1,
        hands_bid=0,
        bids_made=0,
        times_partner=0,
        partner_made=0,
        double_slots=0,
        sweeps=0,
        first_done_at=ended,
        last_done_at=ended,
        ended_at=ended,
    )


async def _table(session: AsyncSession, n: int = 3) -> tuple[User, list[str]]:
    owner = await make_user(session, email="kt@example.com", player_name="Owner")
    return owner, [str((await make_player(session, name=f"T{k}")).id) for k in range(n)]


async def _holders(session: AsyncSession) -> dict[str, list[str]]:
    rows = (await session.execute(select(Crown).where(Crown.game == KT.value))).scalars()
    return {r.kind: sorted(map(str, r.holders)) for r in rows}


def _today_ms(hours_ago: int) -> int:
    """A time today (APP_TZ), at most `hours_ago` back, never before midnight."""
    now = to_ms(now_utc())
    midnight = to_ms(
        datetime.combine(day_of(now, settings.tz), datetime.min.time(), tzinfo=settings.tz)
    )
    return max(now - hours_ago * HOUR, midnight + 1000)


async def test_the_sitara_is_most_wins_today_and_needs_a_win(session: AsyncSession) -> None:
    owner, (a, b, c) = await _table(session)
    first = await _kt_game(session, owner, [(a, 1), (b, 2), (c, 3)], ended_ms=_today_ms(2))
    await reward_game(session, KT, first, void=False)
    assert (await _holders(session)).get("sitara", []) == []
    second = await _kt_game(session, owner, [(a, 1), (b, 2), (c, 3)], ended_ms=_today_ms(1))

    rewards = await reward_game(session, KT, second, void=False)

    # a won both; b and c played two but never won, so only a holds it.
    assert (await _holders(session))["sitara"] == [a]
    assert [(c.kind.value, c.to) for c in rewards.changes] == [("sitara", (a,))]
    feed = (await session.execute(select(Activity).where(Activity.kind == "crown"))).scalars()
    assert [(f.game, f.data["crown"]) for f in feed] == [("kaali-teeri", "sitara")]


async def test_a_bounty_pays_whoever_beats_the_games_badshah(session: AsyncSession) -> None:
    owner, (a, b, c) = await _table(session)
    session.add(
        Crown(game=KT.value, kind="medal1", holders=[uuid.UUID(a)], since=from_ms(0), defences=0)
    )
    await session.flush()
    game = await _kt_game(session, owner, [(b, 1), (a, 2), (c, 3)], ended_ms=_today_ms(1))

    rewards = await reward_game(session, KT, game, void=False)

    stmt = select(SikkeEntry).where(SikkeEntry.kind == "bounty")
    rows = (await session.execute(stmt)).scalars().all()
    assert [(str(r.player_id), r.game, r.amount) for r in rows] == [(b, "kaali-teeri", 40)]
    assert rows[0].key.startswith(f"bounty:kaali-teeri:{a}:{b}:")
    assert [(e.player_id, e.data["holder"]) for e in rewards.bounties] == [(b, a)]


async def test_medals_and_places_of_another_game(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner, (a, b, c) = await _table(session)
    await sign_in(client, owner)
    first = await _kt_game(session, owner, [(a, 1), (b, 2), (c, 3)], ended_ms=_today_ms(2))
    second = await _kt_game(session, owner, [(b, 1), (a, 2), (c, 3)], ended_ms=_today_ms(1))

    medals = (await client.get("/api/medals", params={"game": "kaali-teeri"})).json()
    without = (
        await client.get("/api/medals", params={"game": "kaali-teeri", "excludeGame": str(second)})
    ).json()
    places = (await client.get("/api/places", params={"game": "kaali-teeri"})).json()

    assert set(medals) == {"game", "season", "minGames", "standings", "lastSeason", "today"}
    assert medals["game"] == "kaali-teeri" and medals["minGames"] == 8
    assert {r["player"]["id"]: (r["games"], r["wins"]) for r in medals["standings"]} == {
        a: (2, 1),
        b: (2, 1),
        c: (2, 0),
    }
    assert medals["today"]["games"] == 2
    assert {r["player"]["id"]: r["wins"] for r in without["today"]["rows"]} == {a: 1, b: 0, c: 0}
    assert without["today"]["games"] == 1
    assert [g["gameId"] for g in places["games"]] == [str(second), str(first)]
    assert [(s["playerId"], s["place"]) for s in places["games"][0]["seats"]] == [
        (b, 1),
        (a, 2),
        (c, 3),
    ]
    kachuful = (await client.get("/api/medals")).json()
    assert kachuful["game"] == "kachuful" and "minRounds" in kachuful


async def test_the_50th_game_is_a_group_milestone_told_to_seated_accounts(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[object, str, dict[str, Any]]] = []

    async def record(_: AsyncSession, user_id: object, event: str, data: dict[str, Any]) -> None:
        sent.append((user_id, event, data))

    monkeypatch.setattr(milestones_service, "notify_user", record)
    monkeypatch.setattr(rewards_service, "notify_user", record)
    owner, (a, b, _) = await _table(session)
    me = await make_user(session, email="me@example.com", player_name="Me")
    assert me.player_id is not None
    mark = GROUP_GAME_MARKS[0]
    start = to_ms(now_utc()) - 200 * HOUR
    last = uuid.uuid4()
    for k in range(mark):
        seats = [(str(me.player_id), 1), (a, 2), (b, 3)] if k == mark - 1 else [(a, 1), (b, 2)]
        last = await _kt_game(session, owner, seats, ended_ms=start + k * HOUR)

    rewards = await reward_game(session, KT, last, void=False)

    assert [(m.key, m.n, m.game_id) for m in rewards.milestones] == [
        (f"group:kaali-teeri:{mark}", mark, str(last))
    ]
    assert [(u, e) for u, e, _ in sent if e == "milestone"] == [(me.id, "milestone")]
    body = sent[-1][2]["milestone"]
    assert body["key"] == f"group:kaali-teeri:{mark}" and body["game"] == "kaali-teeri"
    feed = (await session.execute(select(Activity).where(Activity.kind == "milestone"))).scalars()
    assert [f.data["key"] for f in feed] == [f"group:kaali-teeri:{mark}"]

    # Voiding that game moves nothing back until another 50th exists: the milestone goes.
    await session.execute(update(Game).where(Game.id == last).values(deleted_at=now_utc()))
    assert await MilestonesService(session).recompute() == []
    assert (await session.execute(select(Milestone))).scalars().first() is None
    feed = (await session.execute(select(Activity).where(Activity.kind == "milestone"))).scalars()
    assert list(feed) == []


async def test_milestones_list_newest_first_with_a_cursor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner, (a, b, _) = await _table(session)
    await sign_in(client, owner)
    for k, key in enumerate(["first:spades:boston", "first:ishaara:speedrun"]):
        game = await _kt_game(session, owner, [(a, 1), (b, 2)], ended_ms=1_000_000 + k)
        session.add(
            Milestone(
                key=key,
                kind="first",
                game=key.split(":")[1],
                game_id=game,
                players=[uuid.UUID(a)],
                at=from_ms(1_000_000 + k),
                data={"played": key.split(":")[1]},
            )
        )
    await session.flush()

    first = (await client.get("/api/milestones", params={"limit": 1})).json()
    rest = (
        await client.get("/api/milestones", params={"limit": 1, "cursor": first["nextCursor"]})
    ).json()

    assert [m["key"] for m in first["items"]] == ["first:ishaara:speedrun"]
    assert first["items"][0]["n"] is None and first["items"][0]["players"][0]["id"] == a
    assert [m["key"] for m in rest["items"]] == ["first:spades:boston"]
    assert rest["nextCursor"] is None
    assert (await client.get("/api/milestones", params={"cursor": "%%"})).status_code == 422


async def test_milestones_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/milestones")).status_code == 401


async def test_crowns_of_another_game_are_served(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner, (a, b, c) = await _table(session)
    await sign_in(client, owner)
    await _kt_game(session, owner, [(a, 1), (b, 2), (c, 3)], ended_ms=_today_ms(2))
    game = await _kt_game(session, owner, [(a, 1), (b, 2), (c, 3)], ended_ms=_today_ms(1))
    await CrownsService(session).recompute(KT, game_id=game)

    body = (await client.get("/api/crowns", params={"game": "kaali-teeri"})).json()

    sitara = next(i for i in body["items"] if i["kind"] == "sitara")
    assert [h["id"] for h in sitara["holders"]] == [a] and sitara["sinceGame"] == str(game)
