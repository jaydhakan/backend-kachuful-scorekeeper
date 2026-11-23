"""Crowns and their closed reigns, and the facts the Legend and the bounties read."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Crown, CrownReign
from app.domain.crowns import ReignSort
from app.domain.time import to_ms

_ENDED_CREATED = text(
    """
    SELECT id, created_at FROM games
    WHERE kind = 'kachuful' AND ended_at IS NOT NULL AND deleted_at IS NULL
    """
)


class CrownsRepository:
    """Data access for `crowns` and `crown_reigns`."""

    async def all(self, session: AsyncSession, game: str) -> list[Crown]:
        """Every stored title of one game."""
        stmt = select(Crown).where(Crown.game == game)
        return list((await session.execute(stmt)).scalars())

    async def upsert(self, session: AsyncSession, values: Sequence[dict[str, Any]]) -> None:
        """Write titles by (game, kind)."""
        if not values:
            return
        stmt = insert(Crown).values(list(values))
        stmt = stmt.on_conflict_do_update(
            index_elements=[Crown.game, Crown.kind],
            set_={c: stmt.excluded[c] for c in ("holders", "since", "since_game", "defences")},
        )
        await session.execute(stmt)

    async def add_reigns(self, session: AsyncSession, values: Sequence[dict[str, Any]]) -> None:
        """Insert closed reigns (one statement)."""
        if values:
            await session.execute(insert(CrownReign), list(values))

    async def reigns_of(self, session: AsyncSession, game: str, kind: str) -> list[CrownReign]:
        """Every closed reign of one title, oldest first."""
        stmt = (
            select(CrownReign)
            .where(CrownReign.game == game, CrownReign.kind == kind)
            .order_by(CrownReign.since, CrownReign.id)
        )
        return list((await session.execute(stmt)).scalars())

    async def page(
        self,
        session: AsyncSession,
        *,
        game: str,
        kind: str | None,
        sort: ReignSort,
        after: tuple[int, datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[CrownReign]:
        """Closed reigns, newest end first (or most defences first), after a keyset cursor."""
        stmt = select(CrownReign).where(CrownReign.game == game)
        if kind is not None:
            stmt = stmt.where(CrownReign.kind == kind)
        if sort == "defences":
            if after is not None:
                stmt = stmt.where(
                    tuple_(CrownReign.defences, CrownReign.until, CrownReign.id) < after
                )
            stmt = stmt.order_by(CrownReign.defences.desc(), CrownReign.until.desc())
        else:
            if after is not None:
                stmt = stmt.where(tuple_(CrownReign.until, CrownReign.id) < after[1:])
            stmt = stmt.order_by(CrownReign.until.desc())
        stmt = stmt.order_by(CrownReign.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    async def season_champions(self, session: AsyncSession) -> list[str]:
        """One id per closed Kachuful season title (the Legend counts them)."""
        stmt = select(Achievement.player_id).where(
            Achievement.game == "kachuful", Achievement.code == "season_champion"
        )
        return [str(p) for p in (await session.execute(stmt)).scalars()]

    async def ended_created_at(self, session: AsyncSession) -> dict[str, int]:
        """When each ended Kachuful game started (ms): the Taj a bounty counts is the one then."""
        rows = await session.execute(_ENDED_CREATED)
        return {str(gid): to_ms(at) for gid, at in rows}


crowns_repository = CrownsRepository()
