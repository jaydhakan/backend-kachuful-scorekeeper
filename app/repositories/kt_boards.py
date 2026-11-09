"""Kaali Teeri board and stats queries. Boards read kt_player_day; stats read the derived rows."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Game, KtGamePlayer, KtHand, KtPlayerDay
from app.domain.kaali_teeri.game import Sweep
from app.domain.kaali_teeri.leaderboard import KtTally
from app.domain.kaali_teeri.stats import KtHandRow, KtSeatResult, KtStatsGame
from app.domain.time import to_ms


@dataclass(frozen=True)
class _GameMeta:
    created_at: datetime
    ended: bool
    seats: dict[str, KtSeatResult]


def _hand_row(row: KtHand) -> KtHandRow:
    return KtHandRow(
        hand_index=row.hand_index,
        dealer_id=str(row.dealer_id),
        bidder_id=str(row.bidder_id),
        bid=row.bid,
        forced=row.forced,
        trump=row.trump,
        partner_ids=tuple(str(p) for p in row.partner_ids),
        slots=dict(row.slots),
        team_points=row.team_points,
        made=row.made,
        sweep=Sweep(row.sweep),
        done_at=to_ms(row.done_at),
        scores=dict(row.scores),
    )


class KtBoardsRepository:
    """Aggregates for Kaali Teeri boards, stats, games lists and head-to-head."""

    async def tallies(
        self, session: AsyncSession, first_day: date | None, end_day: date | None
    ) -> list[KtTally]:
        """Every player's totals over [first_day, end_day); None bounds mean all time."""
        d = KtPlayerDay
        stmt = select(
            d.player_id,
            func.sum(d.games),
            func.sum(d.wins),
            func.sum(d.points),
            func.sum(d.hands_bid),
            func.sum(d.bids_made),
        ).group_by(d.player_id)
        if first_day is not None:
            stmt = stmt.where(d.day >= first_day)
        if end_day is not None:
            stmt = stmt.where(d.day < end_day)
        rows = await session.execute(stmt)
        return [KtTally(str(r[0]), *(int(v) for v in r[1:])) for r in rows]

    async def _hands(
        self, session: AsyncSession, game_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[KtHandRow]]:
        out: dict[uuid.UUID, list[KtHandRow]] = {gid: [] for gid in game_ids}
        if not game_ids:
            return out
        stmt = (
            select(KtHand)
            .where(KtHand.game_id.in_(game_ids))
            .order_by(KtHand.game_id, KtHand.hand_index)
        )
        for row in (await session.execute(stmt)).scalars():
            out[row.game_id].append(_hand_row(row))
        return out

    async def _stats_games(
        self, session: AsyncSession, metas: dict[uuid.UUID, _GameMeta]
    ) -> list[KtStatsGame]:
        hands = await self._hands(session, list(metas))
        return [
            KtStatsGame(
                game_id=str(gid),
                created_at=to_ms(meta.created_at),
                ended=meta.ended,
                seats=meta.seats,
                hands=tuple(hands[gid]),
            )
            for gid, meta in metas.items()
        ]

    async def player_games(self, session: AsyncSession, player_id: uuid.UUID) -> list[KtStatsGame]:
        """Every live game a player sat in, with its hands (two queries)."""
        gp = KtGamePlayer
        stmt = (
            select(gp.game_id, Game.created_at, Game.status, gp.points, gp.place)
            .join(Game, Game.id == gp.game_id)
            .where(gp.player_id == player_id, Game.deleted_at.is_(None))
        )
        key = str(player_id)
        metas = {
            r[0]: _GameMeta(r[1], r[2] == "finished", {key: KtSeatResult(r[3], r[4])})
            for r in await session.execute(stmt)
        }
        return await self._stats_games(session, metas)

    async def shared_games(
        self, session: AsyncSession, a: uuid.UUID, b: uuid.UUID
    ) -> list[KtStatsGame]:
        """Every live game both players sat in, with its hands (two queries)."""
        ga, gb = aliased(KtGamePlayer), aliased(KtGamePlayer)
        stmt = (
            select(
                ga.game_id, Game.created_at, Game.status, ga.points, ga.place, gb.points, gb.place
            )
            .join(gb, gb.game_id == ga.game_id)
            .join(Game, Game.id == ga.game_id)
            .where(ga.player_id == a, gb.player_id == b, Game.deleted_at.is_(None))
        )
        metas = {r[0]: _shared_meta(r, str(a), str(b)) for r in await session.execute(stmt)}
        return await self._stats_games(session, metas)

    async def player_game_ids(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[tuple[uuid.UUID, datetime]]:
        """A player's live games, newest created first."""
        stmt = (
            select(Game.id, Game.created_at)
            .join(KtGamePlayer, KtGamePlayer.game_id == Game.id)
            .where(KtGamePlayer.player_id == player_id, Game.deleted_at.is_(None))
            .order_by(Game.created_at.desc(), Game.id.desc())
            .limit(limit)
        )
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(Game.created_at < at, and_(Game.created_at == at, Game.id < last_id))
            )
        return [(r[0], r[1]) for r in await session.execute(stmt)]


def _shared_meta(r: Sequence[Any], a: str, b: str) -> _GameMeta:
    seats = {a: KtSeatResult(r[3], r[4]), b: KtSeatResult(r[5], r[6])}
    return _GameMeta(r[1], r[2] == "finished", seats)


kt_boards_repository = KtBoardsRepository()
