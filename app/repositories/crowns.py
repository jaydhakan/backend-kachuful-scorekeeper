"""Crowns and their closed reigns, and the facts the Legend and the bounties read."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import TextClause, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Crown, CrownReign
from app.domain.crowns import ReignSort
from app.domain.enums import ProgressionGame, kinds_of
from app.domain.time import to_ms

_ENDED_CREATED = text(
    """
    SELECT id, created_at FROM games
    WHERE kind = ANY(CAST(:kinds AS text[])) AND ended_at IS NOT NULL AND deleted_at IS NULL
    """
)

# Every other game's finished seats (place: Kaali Teeri's own; null for a team game), as the
# Sikke ledger counts them: a void game has no rows, a replaced Spades seat (`result` null) none.
_RESULTS = {
    ProgressionGame.KAALI_TEERI: """
        SELECT s.game_id, s.ended_at, s.player_id, s.place, s.won, s.seat
        FROM kt_game_players s
        WHERE s.ended_at IS NOT NULL AND s.place IS NOT NULL
    """,
    ProgressionGame.SPADES: """
        SELECT s.game_id, s.finished_at, s.player_id, NULL::smallint, s.result = 'win', s.seat
        FROM spades_game_players s
        WHERE s.result IS NOT NULL
    """,
    ProgressionGame.ISHAARA: """
        SELECT s.game_id, s.finished_at, s.player_id, NULL::smallint, s.won, 0
        FROM ishaara_game_players s
    """,
}


def _results(game: ProgressionGame) -> TextClause:
    # Only constant SQL is joined (the game picks one of `_RESULTS`); values are bound.
    return text(
        f"""
        SELECT r.* FROM ({_RESULTS[game]}) r (game_id, ended_at, player_id, place, won, seat)
        JOIN games g ON g.id = r.game_id
        WHERE g.deleted_at IS NULL
          AND (CAST(:exclude AS uuid) IS NULL OR r.game_id <> :exclude)
          AND (CAST(:only AS uuid) IS NULL OR r.game_id = :only)
          AND (CAST(:range_from AS timestamptz) IS NULL OR r.ended_at >= :range_from)
          AND (CAST(:range_to AS timestamptz) IS NULL OR r.ended_at < :range_to)
        """  # noqa: S608
    )


ResultRow = tuple[str, int, str, int | None, bool, int]
"""A finished seat: (game, ended ms, player, stored place or null, won, seat)."""


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

    async def season_champions(self, session: AsyncSession, game: ProgressionGame) -> list[str]:
        """One id per closed season title of one game (the Legend counts them)."""
        stmt = select(Achievement.player_id).where(
            Achievement.game == game.value, Achievement.code == "season_champion"
        )
        return [str(p) for p in (await session.execute(stmt)).scalars()]

    async def ended_created_at(
        self, session: AsyncSession, game: ProgressionGame
    ) -> dict[str, int]:
        """When each ended game of one game started (ms): the Taj a bounty counts is the one then."""
        kinds = [k.value for k in kinds_of(game)]
        rows = await session.execute(_ENDED_CREATED, {"kinds": kinds})
        return {str(gid): to_ms(at) for gid, at in rows}

    async def results(
        self,
        session: AsyncSession,
        game: ProgressionGame,
        *,
        exclude: uuid.UUID | None = None,
        only: uuid.UUID | None = None,
        range_from: datetime | None = None,
        range_to: datetime | None = None,
    ) -> list[ResultRow]:
        """Every finished seat of a game other than Kachuful, optionally in [from, to), without
        one game (`exclude`) or of one game only (`only`)."""
        params = {"exclude": exclude, "only": only, "range_from": range_from, "range_to": range_to}
        rows = await session.execute(_results(game), params)
        return [
            (str(g), to_ms(at), str(p), place, bool(won), seat)
            for g, at, p, place, won, seat in rows
        ]


crowns_repository = CrownsRepository()
