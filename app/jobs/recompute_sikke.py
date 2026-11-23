"""`recompute-sikke [--dry-run]`: rebuild every player's Sikke from what is stored (run at deploy).

Players go in batches, each its own transaction, then Kachuful's bounties, and the closed
nights' Sitara pay. Idempotent: a kept
entry keeps its `earned_at`. `--dry-run` writes nothing and prints each player's lifetime total,
level and weekly average over their active weeks, to check the prices against real history.
"""

import argparse
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.db.session import get_session_factory
from app.domain.sikke import SikkeEarned, level_of
from app.domain.time import day_of
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.players import players_repository
from app.repositories.sikke import sikke_repository
from app.services.sikke import SikkeService
from app.settings import settings

logger = get_logger(__name__)

HELP = "Rebuild every player's Sikke from stored games and achievements (idempotent)"
BATCH = 100


@dataclass(frozen=True)
class _Total:
    name: str
    lifetime: int
    weeks: int


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--dry-run`: print the totals, write nothing."""
    parser.add_argument("--dry-run", action="store_true", help="Print totals; write nothing")


def _batches(items: Sequence[uuid.UUID]) -> list[Sequence[uuid.UUID]]:
    return [items[k : k + BATCH] for k in range(0, len(items), BATCH)]


async def _player_ids() -> list[uuid.UUID]:
    async with get_session_factory()() as session:
        return await sikke_repository.all_player_ids(session)


async def _write(player_ids: Sequence[uuid.UUID]) -> None:
    for batch in _batches(player_ids):
        async with get_session_factory()() as session:
            await SikkeService(session).recompute_players(batch)
            await app_state_repository.bump(session)
            await session.commit()
    async with get_session_factory()() as session:
        await SikkeService(session).recompute_bounties()
        await SikkeService(session).recompute_all_sitara()
        await session.commit()


def _active_weeks(entries: list[SikkeEarned]) -> int:
    return len({day_of(e.earned_at, settings.tz).isocalendar()[:2] for e in entries})


async def _totals(player_ids: Sequence[uuid.UUID]) -> list[_Total]:
    """What every live player would hold, without writing."""
    totals: list[_Total] = []
    async with get_session_factory()() as session:
        service = SikkeService(session)
        by_player: dict[str, list[SikkeEarned]] = {}
        for e in await service.desired_bounties() + await service.desired_sitara():
            by_player.setdefault(e.player_id, []).append(e)
        for batch in _batches(player_ids):
            players = await players_repository.get_many(session, batch)
            for e in await service.desired_for(batch):
                by_player.setdefault(e.player_id, []).append(e)
            for pid in batch:
                mine = by_player.get(str(pid), [])
                if players[pid].merged_into is None and mine:
                    lifetime = sum(e.amount for e in mine)
                    totals.append(_Total(players[pid].display_name, lifetime, _active_weeks(mine)))
    return sorted(totals, key=lambda t: (-t.lifetime, t.name))


def _print(totals: list[_Total]) -> None:
    sys.stdout.write("player\tlifetime\tlevel\tactive weeks\tper active week\n")
    for t in totals:
        avg = round(t.lifetime / t.weeks)
        sys.stdout.write(f"{t.name}\t{t.lifetime}\t{level_of(t.lifetime).n}\t{t.weeks}\t{avg}\n")


async def run(args: argparse.Namespace) -> int:
    """Rebuild (or with `--dry-run`, print) every player's Sikke."""
    player_ids = await _player_ids()
    if args.dry_run:
        totals = await _totals(player_ids)
        _print(totals)
        sys.stdout.write(f"recompute-sikke: dry run, {len(totals)} players earn Sikke\n")
        return 0
    await _write(player_ids)
    logger.info("sikke recomputed", extra={"players": len(player_ids)})
    sys.stdout.write(f"recompute-sikke: {len(player_ids)} players recomputed\n")
    return 0
