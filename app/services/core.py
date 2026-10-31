"""Health checks."""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.logger import get_logger
from app.schemas.core import Health
from app.settings import settings

logger = get_logger(__name__)

_DB_CHECK_TIMEOUT_S = 2.0


class CoreService:
    """Service-level checks that don't belong to a resource."""

    async def health(self) -> Health:
        """Report whether the database answers, without raising."""
        db_ok = await _database_answers()
        return Health(ok=db_ok, db=db_ok, version=settings.APP_VERSION)


async def _database_answers() -> bool:
    try:
        async with asyncio.timeout(_DB_CHECK_TIMEOUT_S), get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.warning("Health check: database unreachable", exc_info=True)
        return False
    return True
