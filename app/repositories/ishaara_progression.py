"""What Ishaara's achievements and awards read.

The facts loaders (`game_facts`, `career_facts`, `day_facts`, `month_facts`) are P5's: stubs
that read nothing until then. Add their queries here (SQL only, no rules; a fixed number of
queries per call; never `games.payload` for more than the one game `game_facts` loads).
`month_tallies` and `first_finished` feed the win-rate seasons and are done.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IshaaraGamePlayer
from app.domain.ishaara.achievements import IshCareerFacts, IshGameFacts
from app.domain.ishaara.awards import IshDayFacts, IshMonthFacts
from app.domain.progression import WinTally
from app.domain.time import from_ms


class IshaaraProgressionRepository:
    """Facts for Ishaara's achievements, daily awards and seasons."""

    async def game_facts(self, session: AsyncSession, game_id: uuid.UUID) -> IshGameFacts | None:
        """One stored game's facts; None when it is gone or has nothing to earn from (stub)."""
        return None

    async def career_facts(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> IshCareerFacts:
        """Some players' finished games (stub: no games)."""
        return IshCareerFacts(player_ids=tuple(sorted(str(p) for p in player_ids)))

    async def day_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> IshDayFacts:
        """A day's play (stub: nothing read)."""
        return IshDayFacts(start_ms=start_ms, end_ms=end_ms)

    async def month_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> IshMonthFacts:
        """A month's play for the season's own awards (stub: nothing read)."""
        return IshMonthFacts(start_ms=start_ms, end_ms=end_ms)

    async def month_tallies(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> list[WinTally]:
        """Games and wins per player that finished in [start, end) (one query)."""
        # Finished games (an abandoned one writes no rows).
        stmt = (
            select(
                IshaaraGamePlayer.player_id,
                func.count(),
                func.count().filter(IshaaraGamePlayer.won),
            )
            .where(
                IshaaraGamePlayer.finished_at >= from_ms(start_ms),
                IshaaraGamePlayer.finished_at < from_ms(end_ms),
                IshaaraGamePlayer.finished_at.is_not(None),
            )
            .group_by(IshaaraGamePlayer.player_id)
        )
        rows = await session.execute(stmt)
        return [WinTally(str(p), int(games), int(wins)) for p, games, wins in rows]

    async def first_finished(self, session: AsyncSession) -> datetime | None:
        """When the first game finished, if any."""
        stmt = select(func.min(IshaaraGamePlayer.finished_at)).where(
            IshaaraGamePlayer.finished_at.is_not(None)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


ishaara_progression_repository = IshaaraProgressionRepository()
