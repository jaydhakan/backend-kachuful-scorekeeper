"""`prune-empty` (one-off): delete games stored before the rule that a game with no round done leaves no trace."""

import argparse
import sys
import uuid

from app.db.session import get_session_factory
from app.domain.enums import GameKind
from app.logger import get_logger
from app.repositories.games import games_repository
from app.repositories.users import users_repository
from app.services.games import GamesService
from app.services.ishaara_games import IshaaraGamesService
from app.services.kt_games import KtGamesService

logger = get_logger(__name__)

HELP = "Soft-delete every game with no round or hand done (--dry-run only counts them)"
# Kinds with a delete use case; empty means no hand done (Kaali Teeri), no card face up (Ishaara).
KINDS = (GameKind.KACHUFUL, GameKind.KAALI_TEERI, GameKind.ISHAARA)
# The server holds Ishaara games from Start, so one waiting for its clue-givers is not empty yet.
FINISHED_ONLY = frozenset({GameKind.ISHAARA})


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--dry-run`."""
    parser.add_argument("--dry-run", action="store_true", help="Count, change nothing")


async def _delete_one(game_id: uuid.UUID, owner_id: uuid.UUID, kind: GameKind) -> bool:
    """Delete one empty game in its own transaction; False when it is gone or has a hand now."""
    async with get_session_factory()() as session:
        owner = await users_repository.get(session, owner_id)
        row = await games_repository.get(session, game_id, kind=kind)
        # Re-checked: a round may have been uploaded since the list was read.
        if owner is None or row is None or row.deleted_at is not None or row.round_count:
            return False
        if kind in FINISHED_ONLY and row.status != "finished":
            return False
        if kind == GameKind.KAALI_TEERI:
            await KtGamesService(session).delete(game_id, owner)
        elif kind == GameKind.ISHAARA:
            await IshaaraGamesService(session).delete(game_id, owner)
        else:
            await GamesService(session).delete(game_id, owner)
        return True


async def run(args: argparse.Namespace) -> int:
    """One transaction per game, through each kind's delete use case (the same as the app's)."""
    empty: list[tuple[uuid.UUID, uuid.UUID, GameKind]] = []
    async with get_session_factory()() as session:
        for kind in KINDS:
            found = await games_repository.empty_ids(
                session, kind=kind, finished_only=kind in FINISHED_ONLY
            )
            empty += [(game_id, owner_id, kind) for game_id, owner_id in found]
    if args.dry_run:
        sys.stdout.write(f"prune-empty: {len(empty)} games with no round done (dry run)\n")
        return 0
    deleted = 0
    for game_id, owner_id, kind in empty:
        deleted += await _delete_one(game_id, owner_id, kind)
    logger.info("prune-empty finished", extra={"found": len(empty), "deleted": deleted})
    sys.stdout.write(f"prune-empty: {deleted} of {len(empty)} empty games deleted\n")
    return 0
