"""`close-nights` (every 30 min): close each night whose last game ended three hours ago.

A closed night stores its story, posts a `night` feed item, pays its Sitara and tells the accounts
that sat. Idempotent. `--all` closes every past night with no feed items or events (backfill,
then `recompute-sikke`).
"""

import argparse
import sys

from app.db.session import get_session_factory
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.services.nights import NightsService

logger = get_logger(__name__)

HELP = "Close every night whose last game ended 3+ hours ago (idempotent)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--all`: every past night, quietly (no feed items, no stream events)."""
    parser.add_argument(
        "--all", action="store_true", help="Every past night; no feed items or events"
    )


async def run(args: argparse.Namespace) -> int:
    """Close what is due, in one transaction."""
    async with get_session_factory()() as session:
        closed = await NightsService(session).close_due(everything=args.all)
        if closed:
            await app_state_repository.bump(session)
        await session.commit()
    logger.info("nights closed", extra={"n": len(closed), "all": args.all})
    sys.stdout.write(f"close-nights: {len(closed)} nights closed\n")
    return 0
