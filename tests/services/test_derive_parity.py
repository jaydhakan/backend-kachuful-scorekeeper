"""Golden fixtures through the real upload path: the stored derived rows equal the app's."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GamePlayer, RoundScore
from app.domain.time import from_ms, to_ms, to_ms_or_none
from app.services import games as games_module
from app.services.games import GamesService
from tests.domain.golden import GAME_NOW, load, names
from tests.factories import make_player, make_user

VALID_GAMES = names("game-")


async def _upload_fixture(
    session: AsyncSession, name: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], dict[uuid.UUID, str]]:
    fixture = load(name)
    payload = fixture["game"]
    letters = sorted({seat["playerId"] for seat in payload["players"]})
    registry = {letter: await make_player(session, name=f"Fx-{letter}") for letter in letters}
    for seat in payload["players"]:
        seat["playerId"] = str(registry[seat["playerId"]].id)
    owner = await make_user(session, email=f"{name}@example.com", player_name=None)
    monkeypatch.setattr(games_module, "now_utc", lambda: from_ms(GAME_NOW))
    await GamesService(session).upload(uuid.UUID(payload["syncId"]), payload, owner)
    return fixture, {p.id: letter for letter, p in registry.items()}


@pytest.mark.parametrize("name", VALID_GAMES)
async def test_stored_game_players_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    fixture, letter_of = await _upload_fixture(session, name, monkeypatch)

    rows = (await session.execute(select(GamePlayer).order_by(GamePlayer.seat))).scalars().all()

    stored = [
        {
            "gameId": str(r.game_id),
            "playerId": letter_of[r.player_id],
            "localId": r.local_id,
            "seat": r.seat,
            "name": r.name_snapshot,
            "emoji": r.emoji,
            "color": r.color,
            "points": r.points,
            "place": r.place,
            "won": r.won,
            "rounds": r.rounds,
            "made": r.made,
            "missed": r.missed,
            "bestRound": r.best_round,
            "worstRound": r.worst_round,
            "longestHot": r.longest_hot,
            "longestCold": r.longest_cold,
            "hookHits": r.hook_hits,
            "endedAt": to_ms_or_none(r.ended_at),
        }
        for r in rows
    ]
    assert stored == fixture["expect"]["gamePlayers"]


@pytest.mark.parametrize("name", VALID_GAMES)
async def test_stored_round_scores_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    fixture, letter_of = await _upload_fixture(session, name, monkeypatch)

    rows = (await session.execute(select(RoundScore))).scalars().all()

    stored = sorted(
        (
            {
                "gameId": str(r.game_id),
                "roundIndex": r.round_index,
                "playerId": letter_of[r.player_id],
                "doneAt": to_ms(r.done_at),
                "cards": r.cards,
                "maxCards": r.max_cards,
                "trump": r.trump,
                "bid": r.bid,
                "outcome": r.outcome,
                "points": r.points,
                "isDealer": r.is_dealer,
                "hookHits": r.hook_hits,
                "runHot": r.run_hot,
                "runCold": r.run_cold,
                "tableSize": r.table_size,
                "madeCount": r.made_count,
            }
            for r in rows
        ),
        key=lambda r: (r["roundIndex"], r["playerId"]),
    )
    expected = sorted(
        fixture["expect"]["roundScores"], key=lambda r: (r["roundIndex"], r["playerId"])
    )
    assert stored == expected
