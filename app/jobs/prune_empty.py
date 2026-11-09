"""`prune-empty` (one-off): delete games stored before the rule that a game with no round done leaves no trace."""

import argparse
import sys

from app.db.session import get_session_factory
from app.domain.enums import GameKind
from app.logger import get_logger
from app.repositories.games import games_repository
from app.repositories.users import users_repository
from app.services.games import GamesService

logger = get_logger(__name__)

HELP = "Soft-delete every game with no round done (--dry-run only counts them)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--dry-run`."""
    parser.add_argument("--dry-run", action="store_true", help="Count, change nothing")


async def run(args: argparse.Namespace) -> int:
    """One transaction per game, through the same delete use case as the app's."""
    async with get_session_factory()() as session:
        empty = await games_repository.empty_ids(session, kind=GameKind.KACHUFUL)
    if args.dry_run:
        sys.stdout.write(f"prune-empty: {len(empty)} games with no round done (dry run)\n")
        return 0
    deleted = 0
    for game_id, owner_id in empty:
        async with get_session_factory()() as session:
            owner = await users_repository.get(session, owner_id)
            row = await games_repository.get(session, game_id, kind=GameKind.KACHUFUL)
            # Re-checked: a round may have been uploaded since the list was read.
            if owner is None or row is None or row.deleted_at is not None or row.round_count:
                continue
            await GamesService(session).delete(game_id, owner)
            deleted += 1
    logger.info("prune-empty finished", extra={"found": len(empty), "deleted": deleted})
    sys.stdout.write(f"prune-empty: {deleted} of {len(empty)} empty games deleted\n")
    return 0
