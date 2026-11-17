"""`recompute-achievements --game <game>`: rebuild one game's achievements from what is stored.

Walks every live game of that game once, then every player's career, in batches, each batch its
own transaction. Idempotent: a row that still holds is kept (its `earned_at` and feed item stay);
new rows' feed items are dated when they were earned, so a backfill doesn't flood today's feed.
"""

import argparse
import sys
import uuid
from collections.abc import Sequence

from app.db.session import get_session_factory
from app.domain.enums import ProgressionGame, kinds_of
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.services.achievements import AchievementsEngine
from app.services.progression.base import GameProgression
from app.services.progression.registry import progression_of

logger = get_logger(__name__)

HELP = "Rebuild one game's achievements from every stored game (idempotent)"
BATCH = 100


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--game` (required): which game to rebuild."""
    parser.add_argument("--game", required=True, choices=[g.value for g in ProgressionGame])


def _batches[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[k : k + BATCH] for k in range(0, len(items), BATCH)]


async def _targets(rules: GameProgression) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Every live game, and every player seated in one or holding a career row (to clear it)."""
    kinds = kinds_of(rules.game)
    async with get_session_factory()() as session:
        games = await games_repository.live_ids(session, kinds)
        seated = await games_repository.seated_players(session, kinds)
        holders = await achievements_repository.holders(
            session, rules.career_codes, game=rules.game
        )
    return games, sorted(set(seated) | set(holders))


async def _games(rules: GameProgression, game_ids: Sequence[uuid.UUID]) -> int:
    added = 0
    for batch in _batches(game_ids):
        async with get_session_factory()() as session:
            engine = AchievementsEngine(session, backfill=True)
            for game_id in batch:
                added += len(await engine.recompute_game(rules, game_id))
            await app_state_repository.bump(session)
            await session.commit()
    return added


async def _careers(rules: GameProgression, player_ids: Sequence[uuid.UUID]) -> int:
    added = 0
    for batch in _batches(player_ids):
        async with get_session_factory()() as session:
            engine = AchievementsEngine(session, backfill=True)
            added += len(await engine.recompute_careers(rules, set(batch)))
            await app_state_repository.bump(session)
            await session.commit()
    return added


async def run(args: argparse.Namespace) -> int:
    """Rebuild every game's own rows, then every career; prints what was added."""
    rules = progression_of(ProgressionGame(args.game))
    game_ids, player_ids = await _targets(rules)
    from_games = await _games(rules, game_ids)
    from_careers = await _careers(rules, player_ids)
    logger.info(
        "achievements recomputed",
        extra={"game": rules.game.value, "games": len(game_ids), "players": len(player_ids)},
    )
    sys.stdout.write(
        f"recompute-achievements: {rules.game} {len(game_ids)} games, {len(player_ids)} players, "
        f"{from_games + from_careers} rows added\n"
    )
    return 0
