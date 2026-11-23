"""`recompute-crowns [--dry-run]`: rebuild Kachuful's titles from what is stored.

Medals, Sitara and Legend start from now (their past standings aren't kept). Writes no feed items.
Idempotent; run once at deploy, then `recompute-sikke` for the bounties. `--dry-run` prints who
would hold each title.
"""

import argparse
import sys

from app.db.session import get_session_factory
from app.logger import get_logger
from app.services.crowns import CrownsService

logger = get_logger(__name__)

HELP = "Rebuild Kachuful's crowns from what is stored (idempotent, no feed items)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--dry-run`: print the holders, write nothing."""
    parser.add_argument("--dry-run", action="store_true", help="Print holders; write nothing")


async def _print() -> None:
    async with get_session_factory()() as session:
        desired = await CrownsService(session).desired()
    for kind, crown in desired.items():
        sys.stdout.write(f"{kind}\t{','.join(crown.holders) or '-'}\n")
    sys.stdout.write("recompute-crowns: dry run\n")


async def run(args: argparse.Namespace) -> int:
    """Rebuild (or with `--dry-run`, print) every title."""
    if args.dry_run:
        await _print()
        return 0
    async with get_session_factory()() as session:
        changes = await CrownsService(session).recompute(game_id=None, announce=False)
        await session.commit()
    logger.info("crowns recomputed", extra={"changes": len(changes)})
    sys.stdout.write(f"recompute-crowns: {len(changes)} titles changed\n")
    return 0
