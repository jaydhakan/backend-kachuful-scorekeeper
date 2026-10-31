"""`seed-demo`: staging test data the frontend can use (an admin, two members, games, links)."""

import argparse
import random
import sys
import uuid

from sqlalchemy import func, select

from app.db.models import User
from app.db.session import get_session_factory
from app.domain.time import now_utc, to_ms
from app.jobs.fake_games import MINUTE, make_payload
from app.jobs.seeding import add_player, add_user, refuse_on_production, wipe
from app.services.admin import AdminService, issue_invite
from app.services.games import GamesService

HELP = "Fill a non-production database with demo accounts, players and two weeks of games"

ACCOUNTS = [
    ("admin@kachuful.test", "admin-demo", "admin", "Jay"),
    ("riya@kachuful.test", "riya-demo", "member", "Riya"),
    ("kabir@kachuful.test", "kabir-demo", "member", "Kabir"),
]
OTHERS = ["Asha", "Om", "Neha", "Dev", "Zoya", "Ishan"]
GAMES = 40


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """`--reset` empties the database first."""
    parser.add_argument("--reset", action="store_true", help="Empty every table first")


async def run(args: argparse.Namespace) -> int:
    """Idempotent with --reset; without it, refuses when users already exist."""
    if refuse_on_production("seed-demo"):
        return 2
    rng = random.Random(7)
    async with get_session_factory()() as session:
        if args.reset:
            await wipe(session)
        elif (await session.execute(select(func.count()).select_from(User))).scalar_one():
            sys.stdout.write("seed-demo: users exist; run with --reset to start over\n")
            return 2
        users: list[User] = []
        players = []
        for i, (email, password, role, name) in enumerate(ACCOUNTS):
            player = await add_player(session, name, i)
            players.append(player)
            users.append(await add_user(session, email, password, role, player))
        for i, name in enumerate(OTHERS, start=len(ACCOUNTS)):
            player = await add_player(session, name, i)
            player.created_by = users[1].id
            players.append(player)
        await session.commit()
        games = GamesService(session)
        now = to_ms(now_utc())
        for k in range(GAMES):
            table = rng.sample(players, rng.randint(3, 6))
            last = k == GAMES - 1
            start = now - (GAMES - k) * 8 * 60 * MINUTE if not last else now - 20 * MINUTE
            payload = make_payload(
                [str(p.id) for p in table],
                rounds=rng.randint(6, 16) if not last else 5,
                finished=not last,
                start_ms=start,
                seed=k,
                first_dealer=rng.randrange(len(table)),
                step_ms=3 * MINUTE,
            )
            owner = users[k % len(users)]
            await games.upload(uuid.UUID(payload["syncId"]), payload, owner)
        invite = await issue_invite(
            session, email="new.player@kachuful.test", player_id=None, created_by=users[0].id
        )
        await session.commit()
        reset = await AdminService(session).create_reset_link(users[2].id)
    sys.stdout.write("seed-demo: done\n")
    for email, password, role, _ in ACCOUNTS:
        sys.stdout.write(f"  {role:6} {email} / {password}\n")
    sys.stdout.write(f"  pending invite (new.player@kachuful.test): {invite.invite_url}\n")
    sys.stdout.write(f"  reset link (kabir@kachuful.test): {reset.reset_url}\n")
    sys.stdout.write(f"  {GAMES} games; the newest is still being played (good for live links)\n")
    return 0
