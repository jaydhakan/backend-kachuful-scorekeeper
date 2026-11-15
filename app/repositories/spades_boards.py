"""Spades board and stats queries over `spades_game_players` (both kinds), and a player's games
across `spades_seats` (table mode) and `spades_table_seats` (online)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SpadesGamePlayer, SpadesSeat, SpadesTableSeat
from app.domain.spades.stats import SpGameFact, SpTally

_gp = SpadesGamePlayer
# Every column read here is in both indexes' INCLUDE lists, so these are index-only scans.
_FACT_COLUMNS = (
    _gp.game_id,
    _gp.player_id,
    _gp.team,
    _gp.partner_id,
    _gp.result,
    _gp.score,
    _gp.hands,
    _gp.bids,
    _gp.bids_made,
    _gp.nils,
    _gp.nils_made,
    _gp.blind_nils,
    _gp.blind_nils_made,
    _gp.bags,
    _gp.sets,
)


def _fact(r: Sequence[Any]) -> SpGameFact:
    return SpGameFact(
        game_id=str(r[0]),
        player_id=str(r[1]),
        team=r[2],
        partner_id=None if r[3] is None else str(r[3]),
        result=r[4],
        score=r[5],
        tally=SpTally(*r[6:15]),
    )


class SpadesBoardsRepository:
    """Aggregates for Spades boards, stats, head-to-head and a player's games."""

    async def facts_between(
        self, session: AsyncSession, starts: datetime | None, ends: datetime | None
    ) -> list[SpGameFact]:
        """Rows of games finished in [starts, ends); None bounds mean all time (one query)."""
        stmt = select(*_FACT_COLUMNS)
        if starts is not None:
            stmt = stmt.where(_gp.finished_at >= starts)
        if ends is not None:
            stmt = stmt.where(_gp.finished_at < ends)
        return [_fact(r) for r in await session.execute(stmt)]

    async def player_facts(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> list[SpGameFact]:
        """Every row of these players (one query)."""
        stmt = select(*_FACT_COLUMNS).where(_gp.player_id.in_(list(player_ids)))
        return [_fact(r) for r in await session.execute(stmt)]

    async def player_game_ids(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[tuple[uuid.UUID, str, datetime]]:
        """(id, kind, created_at) of a player's live games of both kinds, newest created first."""
        seated = union_all(
            select(SpadesSeat.game_id).where(SpadesSeat.player_id == player_id),
            select(SpadesTableSeat.game_id).where(SpadesTableSeat.player_id == player_id),
        ).subquery("seated")
        stmt = (
            select(Game.id, Game.kind, Game.created_at)
            .join(seated, seated.c.game_id == Game.id)
            .where(Game.deleted_at.is_(None))
            .order_by(Game.created_at.desc(), Game.id.desc())
            .limit(limit)
        )
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(Game.created_at < at, and_(Game.created_at == at, Game.id < last_id))
            )
        return [(r[0], r[1], r[2]) for r in await session.execute(stmt)]


spades_boards_repository = SpadesBoardsRepository()
