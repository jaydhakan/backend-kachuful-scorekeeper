"""Test data builders. Everything is flushed into the test transaction, never committed for real."""

import uuid
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, PasswordReset, Player, User
from app.domain.names import name_key
from app.domain.time import now_utc
from app.security.passwords import hash_password
from app.security.tokens import hash_token, new_token

PASSWORD = "secret1"


async def make_player(
    session: AsyncSession,
    *,
    name: str = "Riya",
    created_by: uuid.UUID | None = None,
    player_id: uuid.UUID | None = None,
    first_name: str | None = None,
    email: str | None = None,
) -> Player:
    """A registry player (no email unless given, like players from before emails)."""
    player = Player(
        id=player_id or uuid.uuid4(),
        display_name=name,
        name_key=name_key(name),
        email=email,
        first_name=first_name,
        emoji="🦊",
        color="#ff8a65",
        created_by=created_by,
    )
    session.add(player)
    await session.flush()
    await session.refresh(player)
    return player


async def make_user(
    session: AsyncSession,
    *,
    email: str = "member@example.com",
    role: str = "member",
    password: str | None = PASSWORD,
    player_name: str | None = "Member",
    active: bool = True,
) -> User:
    """A user, linked to a new player unless `player_name` is None."""
    player = await make_player(session, name=player_name) if player_name else None
    user = User(
        email=email,
        role=role,
        password_hash=hash_password(password) if password else None,
        player_id=player.id if player else None,
        active=active,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    if player is not None:
        player.created_by = user.id
        await session.flush()
    return user


async def make_invite(
    session: AsyncSession,
    *,
    email: str = "new@example.com",
    player_id: uuid.UUID | None = None,
    expires_in: timedelta = timedelta(days=7),
) -> str:
    """A pending invite; returns the raw token."""
    token = new_token()
    session.add(
        Invite(
            email=email,
            token_hash=hash_token(token),
            player_id=player_id,
            expires_at=now_utc() + expires_in,
        )
    )
    await session.flush()
    return token


async def make_reset(
    session: AsyncSession, user: User, *, expires_in: timedelta = timedelta(hours=24)
) -> str:
    """A reset link; returns the raw token."""
    token = new_token()
    session.add(
        PasswordReset(
            user_id=user.id, token_hash=hash_token(token), expires_at=now_utc() + expires_in
        )
    )
    await session.flush()
    return token


async def sign_in(client: httpx.AsyncClient, user: User, password: str = PASSWORD) -> None:
    """Log the client in; its cookie jar keeps the session."""
    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": password}
    )
    assert response.status_code == 200, response.text


def new_player_body(name: str = "Asha", **extra: str) -> dict[str, str]:
    """A NewPlayer request body with a unique email."""
    return {
        "id": str(uuid.uuid4()),
        "displayName": name,
        "email": f"{uuid.uuid4().hex[:10]}@example.com",
        "emoji": "🐯",
        "color": "#60a5fa",
        **extra,
    }
