"""Startup and shutdown."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import dispose_engine
from app.live.hub import hub
from app.logger import configure_logging, get_logger
from app.settings import settings

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging on start; close the pool on stop."""
    configure_logging(settings.LOG_LEVEL)
    logger.info("started", extra={"version": settings.APP_VERSION, "env": settings.APP_ENV})
    yield
    await hub.close()
    await dispose_engine()
    logger.info("stopped")
