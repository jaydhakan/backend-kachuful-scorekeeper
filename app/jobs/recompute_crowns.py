"""`recompute-crowns [--game <game>] [--dry-run]`: rebuild the titles from what is stored.

Medals, Sitara and Legend start from now (their past standings aren't kept). Writes no feed items.
Idempotent; every game unless `--game`. Run once at deploy, then `recompute-sikke` for the
bounties. `--dry-run` prints who would hold each title.
"""

import argparse
import sys

from app.db.session import get_session_factory
from app.domain.enums import ProgressionGame
from app.logger import get_logger
from app.services.crowns import CrownsService

logger = get_logger(__name__)

HELP = "Rebuild every game's crowns from what is stored (idempotent, no feed items)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--game` one game (default every game); `--dry-run` prints the holders, writes nothing."""
    parser.add_argument(
        "--game", choices=[g.value for g in ProgressionGame], help="One game; default every game"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print holders; write nothing")


def _games(args: argparse.Namespace) -> list[ProgressionGame]:
    return [ProgressionGame(args.game)] if args.game else list(ProgressionGame)


async def _print(games: list[ProgressionGame]) -> None:
    async with get_session_factory()() as session:
        service = CrownsService(session)
        for game in games:
            for kind, crown in (await service.desired(game)).items():
                sys.stdout.write(f"{game}\t{kind}\t{','.join(crown.holders) or '-'}\n")
    sys.stdout.write("recompute-crowns: dry run\n")


async def run(args: argparse.Namespace) -> int:
    """Rebuild (or with `--dry-run`, print) every title of the games asked for."""
    games = _games(args)
    if args.dry_run:
        await _print(games)
        return 0
    changed = 0
    async with get_session_factory()() as session:
        service = CrownsService(session)
        for game in games:
            changed += len(await service.recompute(game, game_id=None, announce=False))
        await session.commit()
    logger.info("crowns recomputed", extra={"changes": changed})
    sys.stdout.write(f"recompute-crowns: {changed} titles changed\n")
    return 0
