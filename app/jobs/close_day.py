"""`close-day` (00:05 APP_TZ): finalise yesterday's daily awards of every game, with feed items."""

import argparse
import sys
from datetime import date, timedelta

from app.db.session import get_session_factory
from app.domain.enums import ProgressionGame
from app.logger import get_logger
from app.services.awards import AwardsService, parse_day, today
from app.services.crowns import CrownsService

logger = get_logger(__name__)

HELP = "Freeze a day's awards for every game (default: yesterday in APP_TZ)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--date` closes one past day, `--since` every day from then to yesterday; `--game` one game."""
    parser.add_argument("--date", help="Day to close (YYYY-MM-DD); default yesterday")
    parser.add_argument(
        "--since", help="Close every unclosed day from this one (YYYY-MM-DD) to yesterday"
    )
    parser.add_argument(
        "--game", choices=[g.value for g in ProgressionGame], help="One game; default every game"
    )


def games_of(args: argparse.Namespace) -> list[ProgressionGame]:
    """The game `--game` names, else every game."""
    return [ProgressionGame(args.game)] if args.game else list(ProgressionGame)


def _days(args: argparse.Namespace) -> list[date]:
    yesterday = today() - timedelta(days=1)
    if args.since:
        first = parse_day(args.since)
        return [first + timedelta(days=k) for k in range((yesterday - first).days + 1)]
    return [parse_day(args.date) if args.date else yesterday]


async def _close(day: date, game: ProgressionGame) -> None:
    async with get_session_factory()() as session:
        awards = await AwardsService(session).close_day(day, game)
        await session.commit()
    if awards is None:
        sys.stdout.write(f"close-day: {game} {day} was already closed\n")
        return
    logger.info("day closed", extra={"game": game.value, "day": day.isoformat(), "n": len(awards)})
    sys.stdout.write(f"close-day: {game} {day} closed with {len(awards)} awards\n")


async def recompute_crowns() -> None:
    """Every game's titles after a close: the Sitara clears after midnight, a season moves the
    medals and the Legend."""
    async with get_session_factory()() as session:
        changes = await CrownsService(session).recompute_all()
        await session.commit()
    logger.info("crowns recomputed", extra={"changes": changes})


async def run(args: argparse.Namespace) -> int:
    """Idempotent: a closed day is left alone. Refuses today and the future."""
    days = _days(args)
    if not days or days[-1] >= today():
        sys.stdout.write(f"close-day: {args.since or args.date} has not ended yet\n")
        return 2
    for game in games_of(args):
        for day in days:
            await _close(day, game)
    await recompute_crowns()
    return 0
