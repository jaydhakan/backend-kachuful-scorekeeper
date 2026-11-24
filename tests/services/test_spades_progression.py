"""Spades' real rules through the engine: a scorekeeper upload and an online hand store what the
domain earns (game `spades`), and recomputing changes nothing."""

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Game
from app.domain.enums import GameKind, ProgressionGame
from app.domain.spades.achievements import (
    GAME_CODES,
    MOMENT_CODES,
    game_achievements,
    game_facts_of,
)
from app.services.progression.hooks import progress_game
from app.settings import settings
from tests.factories import make_user, sign_in
from tests.ishaara_builder import ClientFor, client_for_fixture  # noqa: F401 - fixture
from tests.spades_builder import case, seat_registry
from tests.spades_builder import freeze_clock as freeze_spades
from tests.spades_tables_builder import Seats, bid_all, make_seats, play_cards, started
from tests.spades_tables_builder import freeze as freeze_tables

Key = tuple[str, str, int | None]


async def _stored(session: AsyncSession, game_id: str) -> list[Achievement]:
    stmt = select(Achievement).where(Achievement.game_id == uuid.UUID(game_id))
    return list((await session.execute(stmt)).scalars())


def _keys(rows: list[Achievement]) -> list[Key]:
    """The game's own rows (career rows carry the game that earned them too)."""
    own = MOMENT_CODES | GAME_CODES
    return sorted((str(r.player_id), r.code, r.round_index) for r in rows if r.code in own)


async def _expected(session: AsyncSession, kind: str, game_id: str) -> list[Key]:
    payload = await session.get(Game, uuid.UUID(game_id))
    assert payload is not None
    facts = game_facts_of(kind, game_id, payload.payload)
    return sorted(
        (e.player_id, e.code, e.round_index) for e in game_achievements(facts, settings.tz)
    )


async def test_a_scorekeeper_upload_stores_and_returns_what_the_rules_earn(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_spades(monkeypatch)
    registry = await seat_registry(session)
    await sign_in(client, await make_user(session, email="s@example.com", player_name="Scorer"))
    payload = case("won on target")
    game_id = payload["syncId"]

    put = await client.put(f"/api/spades/games/{game_id}", json=payload)
    rows = await _stored(session, game_id)

    assert put.status_code == 200, put.text
    want = await _expected(session, "spades", game_id)
    assert want and {k[1] for k in want} >= {"winner"}
    assert _keys(rows) == want
    assert all(r.game == ProgressionGame.SPADES for r in rows)
    assert all(e["gameKind"] == "spades" for e in put.json()["earned"])
    earned = {(e["playerId"], e["code"]) for e in put.json()["earned"]}
    assert {(p, c) for p, c, _ in want} <= earned
    # Every seated player's first finished game is a game for their career too.
    careers = {e["code"] for e in put.json()["earned"]} - {k[1] for k in want}
    assert careers and all(p in registry for p, _ in earned)


async def test_recomputing_a_stored_game_keeps_every_row(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_spades(monkeypatch)
    registry = await seat_registry(session)
    await sign_in(client, await make_user(session, email="s@example.com", player_name="Scorer"))
    payload = case("won on target")
    await client.put(f"/api/spades/games/{payload['syncId']}", json=payload)
    before = {(r.id, r.earned_at) for r in await _stored(session, payload["syncId"])}
    players = {uuid.UUID(p) for p in registry}

    added = await progress_game(
        session, GameKind.SPADES, uuid.UUID(payload["syncId"]), players, finished=True
    )

    assert added.earned == []
    assert {(r.id, r.earned_at) for r in await _stored(session, payload["syncId"])} == before


@pytest.fixture
async def seats(
    session: AsyncSession, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> Seats:
    freeze_tables(monkeypatch)
    return await make_seats(session, client_for)


async def test_an_online_hand_end_stores_that_hands_moments(
    seats: Seats, session: AsyncSession
) -> None:
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))

    await play_cards(seats)
    rows = await _stored(session, seats.table)

    want = await _expected(session, "spades-online", seats.table)
    assert want and all(index == 0 for _, _, index in want)
    assert _keys(rows) == want
    # Seat 0 holds every spade: the trick-by-trick codes come from the online record.
    assert "one_man_band" in {code for _, code, _ in want}
