"""What Spades's achievements and awards read (SQL only; a fixed number of queries per call).

Both kinds are one game: `game_facts` loads the one stored game it is asked about (its payload);
careers, days and months read `spades_game_players`, written when a game finishes.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Game, SpadesGamePlayer
from app.domain.enums import ProgressionGame
from app.domain.progression import WinTally
from app.domain.spades.achievements import SpCareerFacts, SpGameFacts, SpRow, game_facts_of
from app.domain.spades.awards import DAY_MOMENT_CODES, SpDayFacts, SpMonthFacts
from app.domain.time import from_ms, to_ms

_KINDS = ("spades", "spades-online")
_ROW_COLUMNS = (
    SpadesGamePlayer.game_id,
    SpadesGamePlayer.player_id,
    SpadesGamePlayer.finished_at,
    SpadesGamePlayer.result,
    SpadesGamePlayer.partner_id,
    SpadesGamePlayer.hands,
    SpadesGamePlayer.bids,
    SpadesGamePlayer.bids_made,
    SpadesGamePlayer.nils_made,
    SpadesGamePlayer.blind_nils_made,
    SpadesGamePlayer.bags,
)


def _row(
    game_id: uuid.UUID,
    player_id: uuid.UUID,
    finished_at: datetime,
    result: str | None,
    partner_id: uuid.UUID | None,
    hands: int,
    bids: int,
    made: int,
    nils: int,
    blind: int,
    bags: int,
) -> SpRow:
    return SpRow(
        game_id=str(game_id),
        player_id=str(player_id),
        ended_at=to_ms(finished_at),
        result=result,
        partner_id=None if partner_id is None else str(partner_id),
        hands=hands,
        bids=bids,
        bids_made=made,
        nils_made=nils + blind,
        bags=bags,
    )


def _tables(rows: Iterable[tuple[uuid.UUID, uuid.UUID]]) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = defaultdict(list)
    for game_id, player_id in rows:
        out[str(game_id)].append(str(player_id))
    return {g: tuple(ps) for g, ps in out.items()}


class SpadesProgressionRepository:
    """Facts for Spades's achievements, daily awards and seasons."""

    async def game_facts(self, session: AsyncSession, game_id: uuid.UUID) -> SpGameFacts | None:
        """One stored game's facts; None when it is gone or cancelled (nothing to earn)."""
        stmt = select(Game.kind, Game.payload).where(
            Game.id == game_id, Game.kind.in_(_KINDS), Game.deleted_at.is_(None)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        facts = game_facts_of(row.kind, str(game_id), row.payload)
        return None if facts.status == "cancelled" else facts

    async def career_facts(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> SpCareerFacts:
        """Some players' finished-game rows, and who sat at the end of those games (two queries)."""
        ids = tuple(sorted(str(p) for p in player_ids))
        if not player_ids:
            return SpCareerFacts(player_ids=ids)
        mine = select(*_ROW_COLUMNS).where(SpadesGamePlayer.player_id.in_(player_ids))
        rows = (await session.execute(mine)).all()
        games = {r.game_id for r in rows}
        seated = select(SpadesGamePlayer.game_id, SpadesGamePlayer.player_id).where(
            SpadesGamePlayer.game_id.in_(games), SpadesGamePlayer.result.is_not(None)
        )
        tables = [(g, p) for g, p in (await session.execute(seated)).all()] if games else []
        return SpCareerFacts(
            player_ids=ids, rows=tuple(_row(*r) for r in rows), tables=_tables(tables)
        )

    async def _window_rows(self, session: AsyncSession, start_ms: int, end_ms: int) -> list[SpRow]:
        stmt = select(*_ROW_COLUMNS).where(
            SpadesGamePlayer.finished_at >= from_ms(start_ms),
            SpadesGamePlayer.finished_at < from_ms(end_ms),
        )
        return [_row(*r) for r in (await session.execute(stmt)).all()]

    async def day_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> SpDayFacts:
        """The rows of the games that finished in the day, and their set and Boston moments."""
        rows = await self._window_rows(session, start_ms, end_ms)
        games = {uuid.UUID(r.game_id) for r in rows}
        moments: list[tuple[str, str]] = []
        if games:
            stmt = select(Achievement.player_id, Achievement.code).where(
                Achievement.game_id.in_(games),
                Achievement.game == ProgressionGame.SPADES,
                Achievement.code.in_(DAY_MOMENT_CODES),
            )
            moments = [(str(p), c) for p, c in (await session.execute(stmt)).all()]
        return SpDayFacts(
            start_ms=start_ms, end_ms=end_ms, rows=tuple(rows), moments=tuple(moments)
        )

    async def month_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> SpMonthFacts:
        """The rows of the games that finished in the month (one query)."""
        rows = await self._window_rows(session, start_ms, end_ms)
        return SpMonthFacts(start_ms=start_ms, end_ms=end_ms, rows=tuple(rows))

    async def month_tallies(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> list[WinTally]:
        """Games and wins per player that finished in [start, end) (one query)."""
        # Both kinds' finished games (a cancelled one writes no rows; a draw is a game, not a win).
        stmt = (
            select(
                SpadesGamePlayer.player_id,
                func.count(),
                func.count().filter(SpadesGamePlayer.result == "win"),
            )
            .where(
                SpadesGamePlayer.finished_at >= from_ms(start_ms),
                SpadesGamePlayer.finished_at < from_ms(end_ms),
                SpadesGamePlayer.result.is_not(None),
            )
            .group_by(SpadesGamePlayer.player_id)
        )
        rows = await session.execute(stmt)
        return [WinTally(str(p), int(games), int(wins)) for p, games, wins in rows]

    async def first_finished(self, session: AsyncSession) -> datetime | None:
        """When the first game finished, if any."""
        stmt = select(func.min(SpadesGamePlayer.finished_at)).where(
            SpadesGamePlayer.result.is_not(None)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


spades_progression_repository = SpadesProgressionRepository()
