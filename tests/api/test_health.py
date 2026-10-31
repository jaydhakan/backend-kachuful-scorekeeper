import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.services import core
from app.settings import settings


def _unreachable_engine() -> AsyncEngine:
    return create_async_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none")


async def test_health_with_database_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True, "version": settings.APP_VERSION}


async def test_health_without_database_returns_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core, "get_engine", _unreachable_engine)

    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "db": False, "version": settings.APP_VERSION}
