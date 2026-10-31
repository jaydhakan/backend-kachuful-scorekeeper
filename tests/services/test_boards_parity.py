"""The set fixture through the real upload path: SQL boards and stored achievements equal the app's."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.leaderboard import Metric
from app.domain.time import from_ms, to_ms
from app.services import games as games_module
from app.services.boards import BoardsService
from app.services.games import GamesService
from tests.domain.golden import camel, load
from tests.factories import make_player, make_user

SET = load("set-month")
QUERIES = SET["expect"]["leaderboard"]


async def load_set(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Upload every set game (oldest first); returns registry id → fixture letter id."""
    letters = sorted({s["playerId"] for g in SET["games"] for s in g["players"]})
    # Ids sort like the app's letters, so ties broken by player id fall the same way.
    registry = {
        letter: await make_player(
            session, name=f"S-{letter}", player_id=uuid.UUID(f"00000000-0000-4000-8000-{i:012d}")
        )
        for i, letter in enumerate(letters)
    }
    owner = await make_user(session, email="set@example.com", player_name=None)
    monkeypatch.setattr(games_module, "now_utc", lambda: from_ms(SET["now"]))
    service = GamesService(session)
    for game in sorted(SET["games"], key=lambda g: g["createdAt"]):
        payload = {
            **game,
            "players": [
                {**s, "playerId": str(registry[s["playerId"]].id)} for s in game["players"]
            ],
        }
        await service.upload(uuid.UUID(payload["syncId"]), payload, owner)
    return {str(p.id): letter for letter, p in registry.items()}


def _with_letters(rows: list[dict[str, Any]], letter_of: dict[str, str]) -> list[dict[str, Any]]:
    return [{**r, "playerId": letter_of[r["playerId"]]} for r in rows]


async def test_sql_leaderboards_match_the_app_for_every_metric_and_range(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter_of = await load_set(session, monkeypatch)
    service = BoardsService(session)

    for case in QUERIES:
        query = case["query"]
        board = await service.board(query["from"], query["to"], Metric(query["metric"]), None)

        label = f"{query['range']}/{query['metric']}"
        assert board.min_rounds == case["minRounds"], label
        assert _with_letters(camel(board.rows), letter_of) == case["rows"], label


async def test_stored_achievements_equal_every_achievement_of_the_set(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter_of = await load_set(session, monkeypatch)

    rows = (await session.execute(select(Achievement))).scalars().all()

    def as_app(row: Achievement) -> dict[str, Any]:
        data = dict(row.data)
        if "with" in data:
            data["with"] = letter_of[data["with"]]
        return {
            "code": row.code,
            "playerId": letter_of[str(row.player_id)],
            "gameId": None if row.game_id is None else str(row.game_id),
            "roundIndex": row.round_index,
            "period": row.period,
            "earnedAt": to_ms(row.earned_at),
            "data": data,
        }

    def order(a: dict[str, Any]) -> tuple[Any, ...]:
        return (a["earnedAt"], a["playerId"], a["code"], a["gameId"] or "", a["roundIndex"] or -1)

    stored = sorted((as_app(r) for r in rows), key=order)
    expected = sorted(SET["expect"]["achievements"], key=order)
    assert stored == expected
