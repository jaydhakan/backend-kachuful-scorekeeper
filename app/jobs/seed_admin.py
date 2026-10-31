"""`seed-admin`: create the admin account (no password) and print a one-time invite link."""

import argparse
import sys

from app.db.session import get_session_factory
from app.logger import get_logger
from app.repositories.users import users_repository
from app.services.admin import issue_invite
from app.settings import settings

logger = get_logger(__name__)

HELP = "Create the admin (SEED_ADMIN_EMAIL) and print an invite link to set its password"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No options."""


async def run(_: argparse.Namespace) -> int:
    """Idempotent: a new link each run until the admin has set a password."""
    email = settings.SEED_ADMIN_EMAIL.strip().lower()
    async with get_session_factory()() as session:
        user = await users_repository.get_by_email(session, email)
        if user is not None and user.password_hash is not None:
            sys.stdout.write(
                f"{email} is already set up; use a reset link from the admin screen.\n"
            )
            return 0
        if user is None:
            user = await users_repository.create(
                session, email=email, role="admin", password_hash=None, player_id=None
            )
        user.role = "admin"
        link = await issue_invite(session, email=email, player_id=None, created_by=None)
        await session.commit()
    logger.info("seed-admin issued an invite")
    sys.stdout.write(f"Admin {email}: open this link once to set the password (7 days):\n")
    sys.stdout.write(f"{link.invite_url}\n")
    return 0
