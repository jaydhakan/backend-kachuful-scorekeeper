"""Signing a request in gives its connection back, so a route can take another without starving the pool."""

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import get_session_factory
from app.security.cookies import SESSION_COOKIE
from app.services.auth import AuthService
from app.settings import settings
from tests.factories import make_user, sign_in


async def test_authenticate_returns_its_connection_to_the_pool(
    committed_client: httpx.AsyncClient,
) -> None:
    async with get_session_factory()() as setup:
        user = await make_user(setup, email="pool@example.com")
        await setup.commit()
    await sign_in(committed_client, user)
    token = committed_client.cookies[SESSION_COOKIE]
    # A one-connection pool: a second checkout succeeds only if authenticate released the first.
    engine = create_async_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            ctx = await AuthService(session).authenticate(token)
            async with engine.connect() as other:
                await other.exec_driver_sql("SELECT 1")
    finally:
        await engine.dispose()

    assert ctx is not None and ctx.user.id == user.id
