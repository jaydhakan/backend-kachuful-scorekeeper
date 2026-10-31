"""`python -m app.cli <command>`: the jobs systemd timers run, and one-off admin tasks."""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from types import ModuleType

from app.db.session import dispose_engine, use_job_timeouts
from app.jobs import seed_admin
from app.logger import configure_logging
from app.settings import settings

JOBS: dict[str, ModuleType] = {
    "seed-admin": seed_admin,
}


async def _run(
    job: Callable[[argparse.Namespace], Awaitable[int]], args: argparse.Namespace
) -> int:
    try:
        return await job(args)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run one job."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, module in JOBS.items():
        module.add_arguments(commands.add_parser(name, help=module.HELP))
    args = parser.parse_args(argv)
    configure_logging(settings.LOG_LEVEL)
    use_job_timeouts()
    return asyncio.run(_run(JOBS[args.command].run, args))


if __name__ == "__main__":
    sys.exit(main())
