"""What a finished Ishaara game derives: ishaara_game_players, the ishaara_player_day rollup, and
the board rows read back from them."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import IshaaraGamePlayer, IshaaraPlayerDay
from app.domain.time import to_ms

# One statement rebuilds every (player, day) pair given from the per-game rows; days are APP_TZ.
_REBUILD_DAYS = text(
    """
    INSERT INTO ishaara_player_day (
        player_id, day, games, wins, words_found, wrong_picks, trap_hits, clue_games, clue_wins,
        clues_given, avg_clue_sum, avg_clue_games, penalties
    )
    SELECT t.player_id, t.day, count(*), count(*) FILTER (WHERE gp.won),
           sum(gp.words_found), sum(gp.wrong_picks), count(*) FILTER (WHERE gp.trap_hit),
           count(*) FILTER (WHERE gp.role = 'clue-giver'),
           count(*) FILTER (WHERE gp.role = 'clue-giver' AND gp.won),
           coalesce(sum(gp.clues_given) FILTER (WHERE gp.role = 'clue-giver'), 0),
           coalesce(sum(gp.avg_clue_number) FILTER (WHERE gp.role = 'clue-giver'), 0),
           count(gp.avg_clue_number) FILTER (WHERE gp.role = 'clue-giver'),
           coalesce(sum(gp.penalties) FILTER (WHERE gp.role = 'clue-giver'), 0)
    FROM (
        SELECT DISTINCT player_id, day
        FROM unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    ) t
    JOIN ishaara_game_players gp
      ON gp.player_id = t.player_id
     AND gp.finished_at >= (t.day::timestamp AT TIME ZONE :tz)
     AND gp.finished_at < ((t.day + 1)::timestamp AT TIME ZONE :tz)
    GROUP BY t.player_id, t.day
    """
)

_DELETE_DAYS = text(
    """
    DELETE FROM ishaara_player_day pd
    USING unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    WHERE pd.player_id = u.player_id AND pd.day = u.day
    """
)

_ROW_COLUMNS = (
    IshaaraGamePlayer.game_id,
    IshaaraGamePlayer.player_id,
    IshaaraGamePlayer.team,
    IshaaraGamePlayer.role,
    IshaaraGamePlayer.won,
    IshaaraGamePlayer.words_found,
    IshaaraGamePlayer.wrong_picks,
    IshaaraGamePlayer.trap_hit,
    IshaaraGamePlayer.clues_given,
    IshaaraGamePlayer.avg_clue_number,
    IshaaraGamePlayer.penalties,
    IshaaraGamePlayer.finished_at,
)


def board_row(r: Sequence[Any]) -> dict[str, Any]:
    """A stored row as the domain's camelCase BoardRow."""
    return {
        "gameId": str(r[0]),
        "playerId": str(r[1]),
        "team": r[2],
        "role": r[3],
        "won": r[4],
        "wordsFound": r[5],
        "wrongPicks": r[6],
        "trapHit": r[7],
        "cluesGiven": r[8],
        "avgClueNumber": r[9],
        "penalties": r[10],
        "finishedAt": to_ms(r[11]),
    }


class IshaaraDerivedRepository:
    """Writes Ishaara's derived rows and reads them back for boards."""

    async def moments(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, datetime]]:
        """(player, finished_at) of a game's stored rows, to rebuild their days later."""
        stmt = select(IshaaraGamePlayer.player_id, IshaaraGamePlayer.finished_at).where(
            IshaaraGamePlayer.game_id == game_id
        )
        return [(r[0], r[1]) for r in await session.execute(stmt)]

    async def insert_rows(self, session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
        """A finished game's rows (one executemany)."""
        if rows:
            await session.execute(insert(IshaaraGamePlayer), list(rows))

    async def delete_rows(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a game's rows."""
        await session.execute(delete(IshaaraGamePlayer).where(IshaaraGamePlayer.game_id == game_id))

    async def reseat(
        self, session: AsyncSession, game_id: uuid.UUID, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Point a game's row at another registry player (merge)."""
        await session.execute(
            update(IshaaraGamePlayer)
            .where(IshaaraGamePlayer.game_id == game_id, IshaaraGamePlayer.player_id == from_id)
            .values(player_id=into_id)
        )

    async def rebuild_player_days(
        self, session: AsyncSession, pairs: set[tuple[uuid.UUID, date]], tz: str
    ) -> None:
        """Recompute `ishaara_player_day` for these (player, day) pairs."""
        if not pairs:
            return
        params = {"player_ids": [p for p, _ in pairs], "days": [d for _, d in pairs]}
        await session.execute(_DELETE_DAYS, params)
        await session.execute(_REBUILD_DAYS, {**params, "tz": tz})

    async def player_days(
        self, session: AsyncSession, player_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, date]]:
        """Every (player, day) a player has a rollup row for (merges rebuild them)."""
        stmt = select(IshaaraPlayerDay.player_id, IshaaraPlayerDay.day).where(
            IshaaraPlayerDay.player_id == player_id
        )
        return [(r[0], r[1]) for r in await session.execute(stmt)]

    async def rows_between(
        self, session: AsyncSession, starts: datetime | None, ends: datetime | None
    ) -> list[dict[str, Any]]:
        """Board rows of games finished in [starts, ends); None bounds mean all time."""
        stmt = select(*_ROW_COLUMNS)
        if starts is not None:
            stmt = stmt.where(IshaaraGamePlayer.finished_at >= starts)
        if ends is not None:
            stmt = stmt.where(IshaaraGamePlayer.finished_at < ends)
        stmt = stmt.order_by(IshaaraGamePlayer.finished_at, IshaaraGamePlayer.game_id)
        return [board_row(r) for r in await session.execute(stmt)]

    async def player_rows(
        self, session: AsyncSession, player_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """A player's board rows, oldest first."""
        stmt = (
            select(*_ROW_COLUMNS)
            .where(IshaaraGamePlayer.player_id == player_id)
            .order_by(IshaaraGamePlayer.finished_at, IshaaraGamePlayer.game_id)
        )
        return [board_row(r) for r in await session.execute(stmt)]

    async def shared_rows(
        self, session: AsyncSession, a: uuid.UUID, b: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Both players' rows from the games they both sat in (one join)."""
        other = aliased(IshaaraGamePlayer)
        stmt = (
            select(*_ROW_COLUMNS)
            .join(other, other.game_id == IshaaraGamePlayer.game_id)
            .where(
                IshaaraGamePlayer.player_id.in_([a, b]),
                other.player_id.in_([a, b]),
                other.player_id != IshaaraGamePlayer.player_id,
            )
            .order_by(IshaaraGamePlayer.finished_at, IshaaraGamePlayer.game_id)
        )
        return [board_row(r) for r in await session.execute(stmt)]


ishaara_derived_repository = IshaaraDerivedRepository()
