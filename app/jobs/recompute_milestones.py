"""`recompute-milestones [--dry-run]`: rebuild the group milestones and firsts from what is stored.

Idempotent; writes no feed items and sends no events (a backfill, run once at deploy).
`--dry-run` prints each milestone and its game.
"""

import argparse
import sys

from app.db.session import get_session_factory
from app.logger import get_logger
from app.services.milestones import MilestonesService

logger = get_logger(__name__)

HELP = "Rebuild the group milestones and firsts (idempotent, no feed items)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--dry-run`: print the milestones, write nothing."""
    parser.add_argument("--dry-run", action="store_true", help="Print milestones; write nothing")


async def _print() -> None:
    async with get_session_factory()() as session:
        desired = await MilestonesService(session).desired()
    for m in sorted(desired, key=lambda x: (x.at, x.key)):
        sys.stdout.write(f"{m.key}\t{m.game_id}\t{len(m.players)} players\n")
    sys.stdout.write(f"recompute-milestones: dry run, {len(desired)} milestones\n")


async def run(args: argparse.Namespace) -> int:
    """Rebuild (or with `--dry-run`, print) every milestone."""
    if args.dry_run:
        await _print()
        return 0
    async with get_session_factory()() as session:
        created = await MilestonesService(session).recompute(announce=False)
        await session.commit()
    logger.info("milestones recomputed", extra={"written": len(created)})
    sys.stdout.write(f"recompute-milestones: {len(created)} milestones written\n")
    return 0
