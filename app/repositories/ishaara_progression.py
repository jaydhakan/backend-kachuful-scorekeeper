"""What Ishaara's achievements and awards read: SQL only, a fixed number of queries per call.

Only `game_facts` reads `games.payload`, for its one game; everything else reads the
ishaara_game_players rows (finished, not abandoned games) and `games.summary`.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraAction, IshaaraGamePlayer
from app.domain.enums import GameKind
from app.domain.ishaara.achievements import (
    IshCareerFacts,
    IshCareerRow,
    IshGameFacts,
    game_facts_of,
)
from app.domain.ishaara.awards import IshDayFacts, IshDayGame, IshMonthFacts, IshPlayRow
from app.domain.progression import WinTally
from app.domain.time import from_ms, to_ms

_GP = IshaaraGamePlayer
_PLAY_COLUMNS = (
    _GP.game_id,
    _GP.player_id,
    _GP.team,
    _GP.role,
    _GP.won,
    _GP.words_found,
    _GP.clues_given,
    _GP.trap_hit,
    _GP.finished_at,
)


def _play_row(r: Sequence[Any]) -> IshPlayRow:
    return IshPlayRow(
        game_id=str(r[0]),
        player_id=str(r[1]),
        team=r[2],
        role=r[3],
        won=r[4],
        words_found=r[5],
        clues_given=r[6],
        trap_hit=r[7],
        finished_at=to_ms(r[8]),
    )


def _day_game(r: Sequence[Any]) -> IshDayGame:
    return IshDayGame(
        game_id=str(r[0]),
        ended_by=str(r[1]),
        started_at=None if r[2] is None else to_ms(r[2]),
        finished_at=to_ms(r[3]),
    )


class IshaaraProgressionRepository:
    """Facts for Ishaara's achievements, daily awards and seasons."""

    async def game_facts(self, session: AsyncSession, game_id: uuid.UUID) -> IshGameFacts | None:
        """One stored, finished game's facts; None when it is gone or has nothing to earn from."""
        # The caller has flushed the state it just saved, so this reads it.
        stmt = select(Game.payload).where(
            Game.id == game_id,
            Game.kind == GameKind.ISHAARA,
            Game.deleted_at.is_(None),
            Game.status == "finished",
        )
        payload = (await session.execute(stmt)).scalar_one_or_none()
        return None if payload is None else game_facts_of(payload)

    async def career_facts(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> IshCareerFacts:
        """Every seat of every finished game these players sat in (one query): crew counts them."""
        ids = tuple(sorted(str(p) for p in player_ids))
        if not player_ids:
            return IshCareerFacts(player_ids=ids)
        games = select(_GP.game_id).where(_GP.player_id.in_(player_ids))
        stmt = (
            select(
                _GP.game_id,
                _GP.player_id,
                _GP.finished_at,
                _GP.won,
                _GP.role,
                _GP.words_found,
                _GP.clues_given,
            )
            .where(_GP.game_id.in_(games))
            .order_by(_GP.finished_at, _GP.game_id, _GP.player_id)
        )
        rows = await session.execute(stmt)
        return IshCareerFacts(
            player_ids=ids,
            rows=tuple(
                IshCareerRow(
                    game_id=str(g),
                    player_id=str(p),
                    finished_at=to_ms(at),
                    won=won,
                    role=role,
                    words_found=found,
                    clues_given=clues,
                )
                for g, p, at, won, role, found, clues in rows
            ),
        )

    async def day_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> IshDayFacts:
        """A day's player rows, and each of its games' end and first clue (two queries)."""
        rows = await self._rows_in(session, start_ms, end_ms)
        return IshDayFacts(
            start_ms=start_ms,
            end_ms=end_ms,
            rows=rows,
            games=await self._day_games(session, sorted({r.game_id for r in rows})),
        )

    async def month_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> IshMonthFacts:
        """A month's player rows, for the season's own awards (one query)."""
        rows = await self._rows_in(session, start_ms, end_ms)
        return IshMonthFacts(start_ms=start_ms, end_ms=end_ms, rows=rows)

    async def _rows_in(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> tuple[IshPlayRow, ...]:
        stmt = (
            select(*_PLAY_COLUMNS)
            .where(_GP.finished_at >= from_ms(start_ms), _GP.finished_at < from_ms(end_ms))
            .order_by(_GP.finished_at, _GP.game_id, _GP.player_id)
        )
        return tuple(_play_row(r) for r in await session.execute(stmt))

    async def _day_games(
        self, session: AsyncSession, game_ids: Sequence[str]
    ) -> tuple[IshDayGame, ...]:
        """How each game ended and its first clue's time (the log's first entry is that clue)."""
        if not game_ids:
            return ()
        first_clue = (
            select(func.min(IshaaraAction.at))
            .where(IshaaraAction.game_id == Game.id, IshaaraAction.type == "clue")
            .scalar_subquery()
        )
        stmt = (
            select(Game.id, Game.summary["endedBy"].astext, first_clue, Game.finished_at)
            .where(Game.id.in_([uuid.UUID(g) for g in game_ids]))
            .order_by(Game.id)
        )
        return tuple(_day_game(r) for r in await session.execute(stmt))

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
