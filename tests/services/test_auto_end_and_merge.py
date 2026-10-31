import argparse
import copy
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, GamePlayer, Player, PlayerDay, User
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError
from app.jobs import auto_end
from app.services import games as games_module
from app.services.games import GamesService
from app.services.merge import MergeService
from tests.factories import make_player, make_user
from tests.game_builder import MINUTE, make_payload

HOUR = 60 * MINUTE


async def _upload(
    session: AsyncSession, owner: User, players: list[Player], *, hours_ago: float, **kw: object
) -> dict[str, object]:
    start = to_ms(now_utc()) - int(hours_ago * HOUR)
    payload = make_payload([str(p.id) for p in players], start_ms=start, **kw)  # type: ignore[arg-type]
    await GamesService(session).upload(uuid.UUID(str(payload["syncId"])), payload, owner)
    return payload


async def _players(session: AsyncSession, n: int = 3) -> list[Player]:
    return [await make_player(session, name=f"T{k}") for k in range(n)]


async def test_auto_end_ends_games_idle_for_a_day_at_their_last_round(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await make_user(session)
    players = await _players(session)
    stale = await _upload(session, owner, players, hours_ago=3, finished=False)
    stale_id = uuid.UUID(str(stale["syncId"]))
    later = now_utc() + timedelta(hours=22)
    monkeypatch.setattr(games_module, "now_utc", lambda: later)
    monkeypatch.setattr(auto_end, "now_utc", lambda: later)
    fresh = await _upload(session, owner, players, hours_ago=-21, finished=False, seed=2)

    await auto_end.run(argparse.Namespace())

    ended = await session.get(Game, stale_id, populate_existing=True)
    still_open = await session.get(Game, uuid.UUID(str(fresh["syncId"])), populate_existing=True)
    assert ended is not None and ended.auto_ended and ended.ended_at is not None
    assert to_ms(ended.ended_at) == stale["rounds"][-1]["doneAt"]  # type: ignore[index]
    assert ended.summary["endedAt"] == to_ms(ended.ended_at)
    places = (
        await session.execute(select(GamePlayer.place).where(GamePlayer.game_id == stale_id))
    ).scalars()
    assert all(p is not None for p in places)
    assert still_open is not None and still_open.ended_at is None


async def test_a_later_upload_reopens_an_auto_ended_game(session: AsyncSession) -> None:
    owner = await make_user(session)
    players = await _players(session)
    old = await _upload(session, owner, players, hours_ago=50, finished=False)
    game_id = uuid.UUID(str(old["syncId"]))
    resumed = copy.deepcopy(old)
    resumed["rev"] = 2
    resumed["updatedAt"] = to_ms(now_utc())

    await GamesService(session).upload(game_id, resumed, owner)

    row = await session.get(Game, game_id, populate_existing=True)
    assert row is not None and row.ended_at is None and row.auto_ended is False


async def test_merge_moves_games_rounds_and_days_to_the_kept_player(
    session: AsyncSession,
) -> None:
    owner = await make_user(session)
    dupe, *others = await _players(session)
    keep = await make_player(session, name="Keep")
    payload = await _upload(session, owner, [dupe, *others], hours_ago=2)

    await MergeService(session).merge(dupe.id, keep.id)

    row = await session.get(Game, uuid.UUID(str(payload["syncId"])), populate_existing=True)
    assert row is not None
    assert str(keep.id) in {seat["playerId"] for seat in row.payload["players"]}
    seated = (await session.execute(select(GamePlayer.player_id))).scalars().all()
    assert dupe.id not in seated and keep.id in seated
    dupe_days = await session.execute(
        select(func.count()).select_from(PlayerDay).where(PlayerDay.player_id == dupe.id)
    )
    assert dupe_days.scalar_one() == 0
    await session.refresh(keep)
    await session.refresh(dupe)
    assert keep.games_count == 1 and dupe.games_count == 0


async def test_merge_of_players_from_the_same_table_is_refused(session: AsyncSession) -> None:
    owner = await make_user(session)
    players = await _players(session)
    await _upload(session, owner, players, hours_ago=2)

    with pytest.raises(ApiError) as caught:
        await MergeService(session).merge(players[0].id, players[1].id)

    assert caught.value.status == 422
