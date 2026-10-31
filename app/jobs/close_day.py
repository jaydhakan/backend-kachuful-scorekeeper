"""`close-day` (00:05 APP_TZ): finalise yesterday's daily awards and write their feed items."""

import argparse
import sys
from datetime import date, timedelta

from app.db.session import get_session_factory
from app.logger import get_logger
from app.services.awards import AwardsService, parse_day, today

logger = get_logger(__name__)

HELP = "Freeze a day's awards (default: yesterday in APP_TZ)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--date YYYY-MM-DD` to close a specific past day."""
    parser.add_argument("--date", help="Day to close (YYYY-MM-DD); default yesterday")


async def run(args: argparse.Namespace) -> int:
    """Idempotent: a closed day is left alone. Refuses today and the future."""
    day: date = parse_day(args.date) if args.date else today() - timedelta(days=1)
    if day >= today():
        sys.stdout.write(f"close-day: {day} has not ended yet\n")
        return 2
    async with get_session_factory()() as session:
        awards = await AwardsService(session).close_day(day)
        await session.commit()
    if awards is None:
        sys.stdout.write(f"close-day: {day} was already closed\n")
    else:
        logger.info("day closed", extra={"day": day.isoformat(), "awards": len(awards)})
        sys.stdout.write(f"close-day: {day} closed with {len(awards)} awards\n")
    return 0
