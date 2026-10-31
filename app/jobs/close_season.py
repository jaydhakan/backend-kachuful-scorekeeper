"""`close-season` (1st of the month, 00:10 APP_TZ): finalise last month's standings and awards."""

import argparse
import sys

from app.db.session import get_session_factory
from app.domain.time import month_of, now_utc, to_ms
from app.logger import get_logger
from app.services.awards import AwardsService, last_month, parse_month
from app.settings import settings

logger = get_logger(__name__)

HELP = "Freeze a month's season (default: last month in APP_TZ)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--month YYYY-MM` to close a specific past month."""
    parser.add_argument("--month", help="Month to close (YYYY-MM); default last month")


async def run(args: argparse.Namespace) -> int:
    """Idempotent: a closed season is left alone. Refuses the current month."""
    month = parse_month(args.month) if args.month else last_month()
    if month >= month_of(to_ms(now_utc()), settings.tz):
        sys.stdout.write(f"close-season: {month} has not ended yet\n")
        return 2
    async with get_session_factory()() as session:
        awards = await AwardsService(session).close_season(month)
        await session.commit()
    if awards is None:
        sys.stdout.write(f"close-season: {month} was already closed\n")
    else:
        logger.info("season closed", extra={"season": month, "awards": len(awards)})
        sys.stdout.write(f"close-season: {month} closed with {len(awards)} awards\n")
    return 0
