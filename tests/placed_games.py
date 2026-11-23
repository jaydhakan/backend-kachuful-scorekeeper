"""Kachuful payloads that finish in a chosen order: crowns, bounties and nights depend on places."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Crown
from app.domain.awards import SEASON_MIN_ROUNDS
from app.domain.derive import build_facts
from app.domain.game import game_from_payload
from app.domain.leaderboard import Metric, leaderboard, tallies_from_facts
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


def board_order(payload: dict[str, Any], metric: Metric) -> list[str] | None:
    """A qualifying game's players best first on a season board, or None when two share a rank."""
    facts = build_facts([game_from_payload(payload)], payload["updatedAt"])
    tallies = tallies_from_facts(facts.rounds, facts.players, 0, payload["updatedAt"] + 1)
    rows = leaderboard(tallies.values(), None, metric, SEASON_MIN_ROUNDS).rows
    ranks = [r.rank for r in rows]
    return [r.player_id for r in rows] if ranks == list(range(1, len(rows) + 1)) else None


def taj_payload(order: list[str], *, start_ms: int) -> dict[str, Any]:
    """A game long enough to qualify for the month's board: `order[0]` places first and takes
    the Taj (medal1), leading the board on accuracy."""
    rounds = SEASON_MIN_ROUNDS + 1
    for seed in range(1, MAX_SEED):
        ranks = _seat_ranks(len(order), seed, rounds)
        if ranks is None:
            continue
        seats = [order[rank - 1] for rank in ranks]
        payload = make_payload(seats, start_ms=start_ms, rounds=rounds, seed=seed)
        best = board_order(payload, Metric.ACCURACY)
        if best is not None and best[0] == order[0]:
            return payload
    raise AssertionError("no seed gives distinct places led on accuracy")


def split_payload(ids: list[str], *, start_ms: int, **kw: Any) -> tuple[dict[str, Any], str, str]:
    """A qualifying game whose best accuracy and best average are different players:
    (payload, accuracy leader, average leader)."""
    for seed in range(1, MAX_SEED):
        payload = make_payload(
            ids, start_ms=start_ms, rounds=SEASON_MIN_ROUNDS + 1, seed=seed, **kw
        )
        accuracy = board_order(payload, Metric.ACCURACY)
        average = board_order(payload, Metric.AVG_PER_ROUND)
        if accuracy is not None and average is not None and accuracy[0] != average[0]:
            return payload, accuracy[0], average[0]
    raise AssertionError("no seed splits accuracy from average")


async def backdate_taj(session: AsyncSession, since_ms: int) -> None:
    """The Taj starts when a write sees it: move that back so earlier-started games face it."""
    await session.execute(
        update(Crown).where(Crown.kind == "medal1").values(since=from_ms(since_ms))
    )
