"""Milestones, and the finished games and achievement rows they are computed from."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Activity, Milestone

# Every kind's finished, non-void games with their finished seats, as the Sikke ledger counts them.
_FINISHED = """
    WITH seats AS (
        SELECT game_id, ended_at, player_id FROM game_players
        WHERE ended_at IS NOT NULL AND place IS NOT NULL
        UNION ALL
        SELECT game_id, ended_at, player_id FROM kt_game_players WHERE ended_at IS NOT NULL
        UNION ALL
        SELECT game_id, finished_at, player_id FROM ishaara_game_players
        UNION ALL
        SELECT game_id, finished_at, player_id FROM spades_game_players WHERE result IS NOT NULL
    ), finished AS (
        SELECT s.game_id, g.kind, max(s.ended_at) AS ended_at,
               array_agg(s.player_id ORDER BY s.player_id) AS players
        FROM seats s JOIN games g ON g.id = s.game_id
        WHERE g.deleted_at IS NULL
        GROUP BY s.game_id, g.kind
    )
"""

# The queries below only join constant SQL; every value is a bound parameter.

# Each game's place among every kind's and among its own game's (both Spades kinds are one game),
# only at the marks asked for.
_RANKED = text(
    f"""{_FINISHED}
    , ranked AS (
        SELECT *,
               row_number() OVER (ORDER BY ended_at, game_id) AS n_all,
               row_number() OVER (
                   PARTITION BY CASE WHEN kind = 'spades-online' THEN 'spades' ELSE kind END
                   ORDER BY ended_at, game_id
               ) AS n_game
        FROM finished
    )
    SELECT game_id, kind, ended_at, players, n_all, n_game FROM ranked
    WHERE n_all = ANY(CAST(:all_marks AS int[])) OR n_game = ANY(CAST(:game_marks AS int[]))
    """  # noqa: S608
)

# The earliest finished game with some achievement code, and everyone who earned it there.
_FIRST_CODES = text(
    f"""{_FINISHED}
    , firsts AS (
        SELECT DISTINCT ON (a.game, a.code) a.game, a.code, a.game_id, f.ended_at
        FROM achievements a JOIN finished f ON f.game_id = a.game_id
        WHERE a.code = ANY(CAST(:codes AS text[]))
        ORDER BY a.game, a.code, f.ended_at, a.game_id
    )
    SELECT fi.game, fi.code, fi.game_id, fi.ended_at, array_agg(DISTINCT a.player_id)
    FROM firsts fi
    JOIN achievements a ON a.game = fi.game AND a.code = fi.code AND a.game_id = fi.game_id
    GROUP BY fi.game, fi.code, fi.game_id, fi.ended_at
    """  # noqa: S608
)

_FIRST_TABLE = text(
    f"""{_FINISHED}
    SELECT game_id, ended_at, players FROM finished
    WHERE kind = :kind AND cardinality(players) >= :size
    ORDER BY ended_at, game_id
    LIMIT 1
    """  # noqa: S608
)

RankedRow = tuple[uuid.UUID, str, datetime, list[uuid.UUID], int, int]
"""(game, kind, ended at, players, place among every kind, place among its game)."""
FirstCodeRow = tuple[str, str, uuid.UUID, datetime, list[uuid.UUID]]
"""(game, code, game id, ended at, players who earned it there)."""
FirstTableRow = tuple[uuid.UUID, datetime, list[uuid.UUID]]


class MilestonesRepository:
    """Data access for `milestones`, their feed items and the facts they come from."""

    async def ranked(
        self, session: AsyncSession, *, all_marks: Sequence[int], game_marks: Sequence[int]
    ) -> list[RankedRow]:
        """The finished games at the marks (one query)."""
        rows = await session.execute(
            _RANKED, {"all_marks": list(all_marks), "game_marks": list(game_marks)}
        )
        return [(g, k, at, list(p), int(a), int(n)) for g, k, at, p, a, n in rows]

    async def first_codes(self, session: AsyncSession, codes: Sequence[str]) -> list[FirstCodeRow]:
        """Per (game, code): its earliest finished game and who earned it there (one query)."""
        rows = await session.execute(_FIRST_CODES, {"codes": list(codes)})
        return [(game, code, gid, at, list(p)) for game, code, gid, at, p in rows]

    async def first_table(
        self, session: AsyncSession, kind: str, size: int
    ) -> FirstTableRow | None:
        """The earliest finished game of a kind with at least `size` finished seats."""
        row = (await session.execute(_FIRST_TABLE, {"kind": kind, "size": size})).first()
        return None if row is None else (row[0], row[1], list(row[2]))

    async def all(self, session: AsyncSession) -> list[Milestone]:
        """Every stored milestone."""
        return list((await session.execute(select(Milestone))).scalars())

    async def upsert(self, session: AsyncSession, values: Sequence[dict[str, Any]]) -> None:
        """Write milestones by key (one statement)."""
        if not values:
            return
        stmt = insert(Milestone).values(list(values))
        stmt = stmt.on_conflict_do_update(
            index_elements=[Milestone.key],
            set_={
                c: stmt.excluded[c] for c in ("kind", "game", "game_id", "players", "at", "data")
            },
        )
        await session.execute(stmt)

    async def delete_keys(self, session: AsyncSession, keys: Sequence[str]) -> None:
        """Remove milestones by key."""
        if keys:
            await session.execute(delete(Milestone).where(Milestone.key.in_(list(keys))))

    async def delete_feed(self, session: AsyncSession, keys: Sequence[str]) -> None:
        """Remove the `milestone` feed items of some keys (gone, or moved to another game)."""
        if keys:
            await session.execute(
                delete(Activity).where(
                    Activity.kind == "milestone", Activity.data["key"].astext.in_(list(keys))
                )
            )

    async def page(
        self, session: AsyncSession, *, after: tuple[datetime, str] | None, limit: int
    ) -> list[Milestone]:
        """Milestones, newest first (`at` then key), after a keyset cursor."""
        stmt = select(Milestone)
        if after is not None:
            stmt = stmt.where(tuple_(Milestone.at, Milestone.key) < after)
        stmt = stmt.order_by(Milestone.at.desc(), Milestone.key.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())


milestones_repository = MilestonesRepository()
