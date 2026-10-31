"""The only place an engine or connection pool is created."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.settings import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_statement_timeout_ms = settings.API_STATEMENT_TIMEOUT_MS


def use_job_timeouts() -> None:
    """Switch this process to the job statement timeout; call before the first query."""
    global _statement_timeout_ms
    _statement_timeout_ms = settings.JOB_STATEMENT_TIMEOUT_MS


def build_engine(*, statement_timeout_ms: int) -> AsyncEngine:
    """A new engine with the app's pool and Postgres settings."""
    connect_args = {
        "server_settings": {
            "statement_timeout": str(statement_timeout_ms),
            "application_name": "kachuful-api",
            "timezone": "UTC",
        }
    }
    if settings.APP_ENV == "test":
        # Tests run several event loops; pooled asyncpg connections are bound to one loop.
        return create_async_engine(
            settings.DATABASE_URL, poolclass=NullPool, connect_args=connect_args
        )
    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    """The process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = build_engine(statement_timeout_ms=_statement_timeout_ms)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """The process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session that is closed (and rolled back if uncommitted) afterwards."""
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    """Close every pooled connection (shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
