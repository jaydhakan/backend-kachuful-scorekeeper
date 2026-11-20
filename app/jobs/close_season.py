"""`close-season` (1st of the month, 00:10 APP_TZ): finalise last month's season of every game."""

import argparse
import sys

from app.db.session import get_session_factory
from app.domain.enums import ProgressionGame
from app.domain.time import month_of, now_utc, previous_month, to_ms
from app.jobs.close_day import games_of, recompute_crowns
from app.logger import get_logger
from app.services.awards import AwardsService, last_month, parse_month
from app.settings import settings

logger = get_logger(__name__)

HELP = "Freeze a month's season for every game (default: last month in APP_TZ)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--month` closes one past month, `--since` every month from then on; `--game` one game."""
    parser.add_argument("--month", help="Month to close (YYYY-MM); default last month")
    parser.add_argument(
        "--since", help="Close every unclosed month from this one (YYYY-MM) to last month"
    )
    parser.add_argument(
        "--game", choices=[g.value for g in ProgressionGame], help="One game; default every game"
    )


def _months(args: argparse.Namespace) -> list[str]:
    if not args.since:
        return [parse_month(args.month) if args.month else last_month()]
    first, last = parse_month(args.since), last_month()
    months: list[str] = []
    while last >= first:
        months.insert(0, last)
        last = previous_month(last)
    return months


async def _close(month: str, game: ProgressionGame) -> None:
    async with get_session_factory()() as session:
        awards = await AwardsService(session).close_season(month, game)
        await session.commit()
    if awards is None:
        sys.stdout.write(f"close-season: {game} {month} was already closed\n")
        return
    logger.info("season closed", extra={"game": game.value, "season": month, "n": len(awards)})
    sys.stdout.write(f"close-season: {game} {month} closed with {len(awards)} awards\n")


async def run(args: argparse.Namespace) -> int:
    """Idempotent: a closed season is left alone. Refuses the current month."""
    months = _months(args)
    if not months or months[-1] >= month_of(to_ms(now_utc()), settings.tz):
        sys.stdout.write(f"close-season: {args.since or args.month} has not ended yet\n")
        return 2
    for game in games_of(args):
        for month in months:
            await _close(month, game)
    await recompute_crowns()
    return 0
