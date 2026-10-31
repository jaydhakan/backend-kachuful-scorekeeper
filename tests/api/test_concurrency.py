"""Two uploads of one game at once: one wins, the other is stale, no duplicate rows (plan §12.2)."""

import asyncio
import copy
import uuid

import httpx
from sqlalchemy import func, select

from app.db.models import GamePlayer, RoundScore
from app.db.session import get_session_factory
from app.domain.time import now_utc, to_ms
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload


async def test_two_simultaneous_uploads_leave_one_winner_and_no_duplicates(
    committed_client: httpx.AsyncClient,
) -> None:
    async with get_session_factory()() as session:
        user = await make_user(session)
        players = [await make_player(session, name=f"C{k}") for k in range(3)]
        await session.commit()
    await sign_in(committed_client, user)
    first = make_payload(
        [str(p.id) for p in players], start_ms=to_ms(now_utc()) - 60 * MINUTE, seed=1
    )
    second = copy.deepcopy(first)
    second["ui"] = {"view": "summary", "bidIdx": 2}
    path = f"/api/games/{first['syncId']}"

    results = await asyncio.gather(
        committed_client.put(path, json=first), committed_client.put(path, json=second)
    )

    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "stale"
    game_id = uuid.UUID(first["syncId"])
    async with get_session_factory()() as session:
        seats = await session.execute(
            select(func.count()).select_from(GamePlayer).where(GamePlayer.game_id == game_id)
        )
        rows = await session.execute(
            select(func.count()).select_from(RoundScore).where(RoundScore.game_id == game_id)
        )
        assert seats.scalar_one() == 3
        assert rows.scalar_one() == 12
