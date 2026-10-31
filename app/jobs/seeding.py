"""Shared helpers for seed jobs (never on production)."""

import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.domain.names import name_key
from app.security.passwords import hash_password
from app.settings import settings

EMOJI = ["🦊", "🐯", "🐼", "🐸", "🦁", "🐨", "🐵", "🦉", "🐙", "🦄", "🐺", "🐻"]
COLORS = ["#ff8a65", "#60a5fa", "#34d399", "#f472b6", "#facc15", "#a78bfa", "#f87171", "#2dd4bf"]


def refuse_on_production(job: str) -> bool:
    """True (and a message) when this is production."""
    if settings.APP_ENV == "production":
        sys.stdout.write(f"{job}: refused on production\n")
        return True
    return False


async def wipe(session: AsyncSession) -> None:
    """Empty every data table (staging/local reset)."""
    names = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename NOT IN ('alembic_version', 'app_state')"
            )
        )
    ).scalars()
    await session.execute(text(f"TRUNCATE {', '.join(names)} CASCADE"))
    await session.execute(text("UPDATE app_state SET data_version = data_version + 1"))


async def add_player(session: AsyncSession, name: str, index: int) -> Player:
    """A registry player with a stable emoji, colour and a test email (<name>@kachuful.test)."""
    player = Player(
        id=uuid.uuid4(),
        display_name=name,
        name_key=name_key(name),
        email=f"{name_key(name).replace(' ', '.')}@kachuful.test",
        emoji=EMOJI[index % len(EMOJI)],
        color=COLORS[index % len(COLORS)],
    )
    session.add(player)
    await session.flush()
    return player


async def add_user(
    session: AsyncSession, email: str, password: str | None, role: str, player: Player | None
) -> User:
    """An account (linked to a player when given)."""
    user = User(
        email=email,
        role=role,
        password_hash=hash_password(password) if password else None,
        player_id=None if player is None else player.id,
    )
    session.add(user)
    await session.flush()
    if player is not None:
        player.created_by = user.id
    return user
