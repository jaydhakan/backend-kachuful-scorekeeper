"""Kachuful payloads that finish in a chosen order: crowns, bounties and nights depend on places."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Crown
from app.domain.awards import SEASON_MIN_ROUNDS
from app.domain.game import game_from_payload
from app.domain.rules import standings
from app.domain.time import from_ms
from app.jobs.fake_games import make_payload

MAX_SEED = 500


def _seat_ranks(n: int, seed: int, rounds: int) -> list[int] | None:
    """Each seat's rank for a seed, or None when two seats tie or the winner has no points."""
    payload = make_payload([f"x{k}" for k in range(n)], start_ms=0, rounds=rounds, seed=seed)
    table = standings(game_from_payload(payload))
    ranks = {s.seat_index: s.rank for s in table}
    out = [ranks[k] for k in range(n)]
    return out if len(set(out)) == n and table[0].total > 0 else None


def placed_payload(
    order: list[str], *, start_ms: int, rounds: int = 4, **kw: Any
) -> dict[str, Any]:
    """A finished game where `order[0]` places first, `order[1]` second, and so on."""
    for seed in range(1, MAX_SEED):
        ranks = _seat_ranks(len(order), seed, rounds)
        if ranks is not None:
            seats = [order[rank - 1] for rank in ranks]
            return make_payload(seats, start_ms=start_ms, rounds=rounds, seed=seed, **kw)
    raise AssertionError("no seed gives distinct places")


def taj_payload(order: list[str], *, start_ms: int) -> dict[str, Any]:
    """A game long enough to qualify for the month's board: `order[0]` takes the Taj (medal1)."""
    return placed_payload(order, start_ms=start_ms, rounds=SEASON_MIN_ROUNDS + 1)


async def backdate_taj(session: AsyncSession, since_ms: int) -> None:
    """The Taj starts when a write sees it: move that back so earlier-started games face it."""
    await session.execute(
        update(Crown).where(Crown.kind == "medal1").values(since=from_ms(since_ms))
    )
