"""`auto-end` abandons Ishaara games idle for 2 hours (no derived rows); it is idempotent."""

import argparse
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraGamePlayer
from app.domain.time import now_utc
from app.jobs import auto_end
from tests.ishaara_builder import service_game, service_table


async def _idle(session: AsyncSession, game_id: uuid.UUID, hours: float) -> None:
    await session.execute(
        update(Game).where(Game.id == game_id).values(updated_at=now_utc() - timedelta(hours=hours))
    )


async def test_auto_end_abandons_games_idle_for_two_hours(session: AsyncSession) -> None:
    table = await service_table(session)
    idle, fresh = (await service_game(session, table))[0], (await service_game(session, table))[0]
    await _idle(session, idle, 2.5)
    await _idle(session, fresh, 1.5)

    await auto_end.run(argparse.Namespace())
    await auto_end.run(argparse.Namespace())

    ended = await session.get(Game, idle, populate_existing=True)
    still = await session.get(Game, fresh, populate_existing=True)
    assert ended is not None and ended.status == "finished" and ended.auto_ended
    state = ended.payload
    assert (state["status"], state["winner"], state["endedBy"]) == ("finished", None, "abandoned")
    assert state["solution"] is not None and len(state["solution"]) == 25
    assert state["rev"] == 4
    rows = await session.execute(select(func.count()).select_from(IshaaraGamePlayer))
    assert rows.scalar_one() == 0
    assert still is not None and still.status == "active"
