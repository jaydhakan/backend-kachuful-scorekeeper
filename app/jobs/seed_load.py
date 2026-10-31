"""`seed-load`: the performance dataset (plan §15.1): 50 players, 5,000 games, ~80,000 rounds."""

import argparse
import random
import sys
import time
import uuid

from sqlalchemy import func, select

from app.db.models import Game
from app.db.session import get_session_factory
from app.domain.time import now_utc, to_ms
from app.jobs.fake_games import MINUTE, make_payload
from app.jobs.seeding import add_player, add_user, refuse_on_production, wipe
from app.services.games import GamesService

HELP = "Fill a non-production database with the load dataset (through the real upload path)"
DAY_MS = 24 * 60 * MINUTE
BATCH = 50


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Sizes, and `--reset` to empty the database first."""
    parser.add_argument("--players", type=int, default=50)
    parser.add_argument("--games", type=int, default=5000)
    parser.add_argument("--days", type=int, default=365, help="Spread games over this many days")
    parser.add_argument("--reset", action="store_true", help="Empty every table first")


async def run(args: argparse.Namespace) -> int:
    """Uploads every game through GamesService, so derived rows and achievements are real."""
    if refuse_on_production("seed-load"):
        return 2
    rng = random.Random(42)
    started = time.perf_counter()
    async with get_session_factory()() as session:
        if args.reset:
            await wipe(session)
        elif (await session.execute(select(func.count()).select_from(Game))).scalar_one():
            sys.stdout.write("seed-load: games exist; run with --reset to start over\n")
            return 2
        players = [await add_player(session, f"Load{i:02d}", i) for i in range(args.players)]
        owners = [
            await add_user(session, f"load{i}@kachuful.test", "load-demo", "member", players[i])
            for i in range(5)
        ]
        await session.commit()
        games = GamesService(session)
        now = to_ms(now_utc()) - 2 * 60 * MINUTE
        span = args.days * DAY_MS
        for k in range(args.games):
            table = rng.sample(players, rng.randint(3, 7))
            payload = make_payload(
                [str(p.id) for p in table],
                rounds=rng.randint(8, 24),
                start_ms=now - span + int(span * k / args.games),
                seed=k,
                first_dealer=rng.randrange(len(table)),
                step_ms=3 * MINUTE,
            )
            await games.upload(uuid.UUID(payload["syncId"]), payload, owners[k % len(owners)])
            if (k + 1) % 500 == 0:
                rate = (k + 1) / (time.perf_counter() - started)
                sys.stdout.write(f"seed-load: {k + 1}/{args.games} games ({rate:.0f}/s)\n")
                sys.stdout.flush()
    sys.stdout.write(f"seed-load: done in {time.perf_counter() - started:.0f}s\n")
    return 0
