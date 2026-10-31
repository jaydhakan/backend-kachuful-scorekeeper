"""Fixed-window counters."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimit


class RateLimitsRepository:
    """Data access for `rate_limits`."""

    async def hit(self, session: AsyncSession, key: str, window_start: datetime) -> int:
        """Count one request in a window and return the window's count (one upsert)."""
        stmt = (
            insert(RateLimit)
            .values(key=key, window_start=window_start, count=1)
            .on_conflict_do_update(
                index_elements=[RateLimit.key, RateLimit.window_start],
                set_={"count": RateLimit.count + 1},
            )
            .returning(RateLimit.count)
        )
        count: int = (await session.execute(stmt)).scalar_one()
        return count


rate_limits_repository = RateLimitsRepository()
