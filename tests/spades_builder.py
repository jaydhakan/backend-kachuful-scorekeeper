"""Spades test games: the app's golden fixtures, seated with real registry players.

The fixtures seat players `00000000-0000-4000-8000-00000000000N`; `seat_registry` makes registry
players with exactly those ids, so table-mode games and online states upload as they are.
"""

import copy
import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Player, SpadesTableSeat, User
from app.domain.enums import GameKind
from app.domain.spades import new_table
from app.domain.spades.stats import player_rows
from app.domain.time import from_ms
from app.repositories.spades_derived import spades_derived_repository
from app.services import spades_boards, spades_games
from app.services.spades_derive import game_player_dicts
from app.services.spades_state import apply_state
from tests.domain.golden import load
from tests.domain.test_spades_golden import Table, _step
from tests.factories import make_player

SP_NOW = 1_794_938_400_000  # 2026-11-17T18:00Z: when the app evaluated spades-validate
NAMES = ("Riya", "Arjun", "Dev", "Meera", "Kabir")

Registry = dict[str, Player]


def fixture_pid(n: int) -> str:
    """The fixtures' registry id for player n (1-5)."""
    return f"00000000-0000-4000-8000-{n:012d}"


def freeze_clock(monkeypatch: pytest.MonkeyPatch, ms: int = SP_NOW) -> None:
    """Uploads are validated, and boards built, at `ms`."""
    monkeypatch.setattr(spades_games, "now_utc", lambda: from_ms(ms))
    monkeypatch.setattr(spades_boards, "now_utc", lambda: from_ms(ms))


async def seat_registry(session: AsyncSession) -> Registry:
    """The five fixture players, by fixture id."""
    registry: Registry = {}
    for n, name in enumerate(NAMES, start=1):
        pid = fixture_pid(n)
        registry[pid] = await make_player(session, name=f"Sp-{name}", player_id=uuid.UUID(pid))
    return registry


def case(name: str) -> dict[str, Any]:
    """A spades-validate case's game under a fresh sync id."""
    found = next(c for c in load("spades-validate")["cases"] if c["name"] == name)
    game: dict[str, Any] = copy.deepcopy(found["game"])
    return game | {"syncId": str(uuid.uuid4())}


def finished_state(name: str) -> dict[str, Any]:
    """A golden online fixture's final state under a fresh id."""
    state: dict[str, Any] = copy.deepcopy(load(name)["expect"]["state"])
    return state | {"id": str(uuid.uuid4())}


def replay(name: str, steps: int) -> Table:
    """A golden online fixture after its first `steps` steps: (state, private hands)."""
    fixture = load(name)
    table: Table = (new_table(**fixture["setup"]), None)
    for step in fixture["steps"][:steps]:
        table, _ = _step(table, step)
    return table


async def store_table(session: AsyncSession, owner: User, state: dict[str, Any]) -> Game:
    """An online table's games row, its seats, and (finished with a result) its player rows."""
    row = Game(id=uuid.UUID(state["id"]), kind=GameKind.SPADES_ONLINE, owner_id=owner.id)
    apply_state(row, state)
    session.add(row)
    await session.flush()
    seated = {s["who"]["playerId"]: i for i, s in enumerate(state["seats"]) if s["who"]}
    # Someone replaced mid-game keeps a seat row with no seat: their hands are theirs.
    left = {p for h in state["history"] for p in h.get("players") or [] if p and p not in seated}
    session.add_all(
        SpadesTableSeat(game_id=row.id, player_id=uuid.UUID(pid), seat=seat)
        for pid, seat in [*seated.items(), *((p, None) for p in sorted(left))]
    )
    result = state["result"]
    if state["status"] == "finished" and result is not None:
        seats = [None if s["who"] is None else s["who"]["playerId"] for s in state["seats"]]
        rows = player_rows(state["history"], seats, result)
        finished: datetime = from_ms(state.get("finishedAt") or state["updatedAt"])
        dicts = game_player_dicts(row.id, row.kind, rows, finished, result["endedBy"])
        await spades_derived_repository.replace_players(session, row.id, dicts)
    await session.flush()
    return row
