"""Test harness: a real Postgres, one rolled-back transaction per test."""

import inspect
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://kachuful:kachuful@localhost:5433/kachuful_test"
)
os.environ["APP_ORIGIN"] = "http://test"

import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker  # noqa: E402

from app.bootstrap.app import create_app  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.session import build_engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://test"
WRITE_HEADERS = {"X-Kachuful": "1", "Origin": ORIGIN}


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makeitem(collector: pytest.Collector, name: str, obj: object) -> None:
    """Mark every async test for anyio before parametrisation sees it."""
    if name.startswith("test_") and inspect.iscoroutinefunction(obj):
        pytest.mark.anyio(obj)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Tests run on asyncio only."""
    return "asyncio"


def alembic_config() -> Config:
    """Alembic config pointing at this repo."""
    return Config(str(ROOT / "alembic.ini"))


async def _reset_schema() -> None:
    engine = build_engine(statement_timeout_ms=60000)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("DROP EXTENSION IF EXISTS citext CASCADE"))
        await conn.execute(text("DROP EXTENSION IF EXISTS pg_trgm CASCADE"))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    """Fresh schema from the migrations, once per run."""
    import anyio

    anyio.run(_reset_schema)
    command.upgrade(alembic_config(), "head")
    yield


@pytest.fixture
async def db_conn() -> AsyncIterator[AsyncConnection]:
    """A connection inside a transaction that is rolled back after the test."""
    engine = build_engine(statement_timeout_ms=10000)
    async with engine.connect() as conn:
        trans = await conn.begin()
        yield conn
        await trans.rollback()
    await engine.dispose()


@pytest.fixture
async def session_factory(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> async_sessionmaker[AsyncSession]:
    """Every session the app opens joins the test transaction (commits become savepoints)."""
    factory = async_sessionmaker(
        bind=db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(db_session, "_session_factory", factory)
    return factory


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session for arranging data and asserting on it."""
    async with session_factory() as s:
        yield s


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client for the app, sending the CSRF headers a browser would."""
    transport = httpx.ASGITransport(app=create_app(), client=("203.0.113.7", 50000))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=WRITE_HEADERS) as c:
        yield c
