"""`auto-end` (every 15 min): end active games idle for 24 hours at their last done round."""

import argparse
import sys
from datetime import timedelta

from app.db.session import get_session_factory
from app.domain.derive import AUTO_END_MS
from app.domain.time import now_utc
from app.logger import get_logger
from app.repositories.games import games_repository
from app.services.games import GamesService

logger = get_logger(__name__)

HELP = "End games idle for 24 hours (sets ended_at to the last done round)"
BATCH = 200


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No options."""


async def run(_: argparse.Namespace) -> int:
    """One short transaction per game, so a failure never holds back the rest."""
    idle_before = now_utc() - timedelta(milliseconds=AUTO_END_MS)
    async with get_session_factory()() as session:
        candidates = await games_repository.auto_end_candidates(session, idle_before, BATCH)
    ended = 0
    for game_id in candidates:
        async with get_session_factory()() as session:
            await games_repository.lock(session, game_id)
            row = await games_repository.get(session, game_id)
            # Re-checked under the lock: an upload may have reopened or ended it meanwhile.
            if row is None or row.ended_at is not None or row.deleted_at is not None:
                continue
            derived = await GamesService(session).rederive(row, row.payload)
            await session.commit()
            ended += derived.ended.at is not None
    logger.info("auto-end finished", extra={"candidates": len(candidates), "ended": ended})
    sys.stdout.write(f"auto-end: {ended} of {len(candidates)} idle games ended\n")
    return 0
