"""Rows derived from games: game_players, round_scores, player_day and players' counters."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GamePlayer, RoundScore

# One statement rebuilds every (player, day) pair given; days are APP_TZ calendar days.
_REBUILD_PLAYER_DAYS = text(
    """
    INSERT INTO player_day (
        player_id, day, rounds, points, made, missed, best_round, worst_round,
        max_hot, max_cold, hook_hits, games, games_ended, wins
    )
    SELECT t.player_id, t.day, r.rounds, r.points, r.made, r.missed, r.best_round, r.worst_round,
           r.max_hot, r.max_cold, r.hook_hits, g.games, e.games_ended, e.wins
    FROM (
        SELECT DISTINCT player_id, day,
               (day::timestamp AT TIME ZONE :tz) AS starts,
               ((day + 1)::timestamp AT TIME ZONE :tz) AS ends
        FROM unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    ) t
    CROSS JOIN LATERAL (
        SELECT count(*) AS rounds,
               coalesce(sum(rs.points), 0) AS points,
               count(*) FILTER (WHERE rs.outcome = 'made') AS made,
               count(*) FILTER (WHERE rs.outcome = 'missed') AS missed,
               max(rs.points) AS best_round,
               min(rs.points) AS worst_round,
               coalesce(max(rs.run_hot), 0) AS max_hot,
               coalesce(max(rs.run_cold), 0) AS max_cold,
               coalesce(sum(rs.hook_hits), 0) AS hook_hits
        FROM round_scores rs
        WHERE rs.player_id = t.player_id AND rs.done_at >= t.starts AND rs.done_at < t.ends
    ) r
    CROSS JOIN LATERAL (
        SELECT count(*) AS games
        FROM game_players gp
        WHERE gp.player_id = t.player_id
          AND gp.first_done_at >= t.starts AND gp.first_done_at < t.ends
    ) g
    CROSS JOIN LATERAL (
        SELECT count(*) AS games_ended, count(*) FILTER (WHERE gp.won) AS wins
        FROM game_players gp
        WHERE gp.player_id = t.player_id AND gp.place IS NOT NULL
          AND gp.ended_at >= t.starts AND gp.ended_at < t.ends
    ) e
    WHERE r.rounds > 0 OR g.games > 0 OR e.games_ended > 0
    """
)

_DELETE_PLAYER_DAYS = text(
    """
    DELETE FROM player_day pd
    USING unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    WHERE pd.player_id = u.player_id AND pd.day = u.day
    """
)

_REFRESH_PLAYER_COUNTERS = text(
    """
    UPDATE players p
    SET games_count = s.games, last_played_at = s.last_played_at
    FROM (
        SELECT ids.id,
               (SELECT count(*) FROM game_players gp WHERE gp.player_id = ids.id) AS games,
               (SELECT max(gp.last_done_at) FROM game_players gp
                WHERE gp.player_id = ids.id) AS last_played_at
        FROM unnest(CAST(:player_ids AS uuid[])) AS ids(id)
    ) s
    WHERE p.id = s.id
    """
)


class DerivedRepository:
    """Writes and rebuilds the derived tables."""

    async def touched_moments(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, datetime]]:
        """(player, time) of every stored round and game end, to rebuild their days later."""
        rounds = select(RoundScore.player_id, RoundScore.done_at).where(
            RoundScore.game_id == game_id
        )
        ends = select(GamePlayer.player_id, GamePlayer.ended_at).where(
            GamePlayer.game_id == game_id, GamePlayer.ended_at.is_not(None)
        )
        result = await session.execute(rounds.union_all(ends))
        return [(row[0], row[1]) for row in result]

    async def player_ids_in_game(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Registry ids seated in a stored game."""
        stmt = select(GamePlayer.player_id).where(GamePlayer.game_id == game_id)
        return list((await session.execute(stmt)).scalars())

    async def replace_rows(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        game_players: Sequence[dict[str, Any]],
        round_scores: Sequence[dict[str, Any]],
    ) -> None:
        """Delete and re-insert a game's derived rows (two executemany inserts)."""
        await self.delete_rows(session, game_id)
        if game_players:
            await session.execute(insert(GamePlayer), list(game_players))
        if round_scores:
            await session.execute(insert(RoundScore), list(round_scores))

    async def delete_rows(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a game's derived rows."""
        await session.execute(delete(RoundScore).where(RoundScore.game_id == game_id))
        await session.execute(delete(GamePlayer).where(GamePlayer.game_id == game_id))

    async def rebuild_player_days(
        self, session: AsyncSession, pairs: set[tuple[uuid.UUID, date]], tz: str
    ) -> None:
        """Recompute `player_day` for these (player, day) pairs from round_scores and game_players."""
        if not pairs:
            return
        params = {"player_ids": [p for p, _ in pairs], "days": [d for _, d in pairs]}
        await session.execute(_DELETE_PLAYER_DAYS, params)
        await session.execute(_REBUILD_PLAYER_DAYS, {**params, "tz": tz})

    async def refresh_player_counters(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> None:
        """Recount `players.games_count` and `last_played_at`."""
        if player_ids:
            await session.execute(_REFRESH_PLAYER_COUNTERS, {"player_ids": list(player_ids)})


derived_repository = DerivedRepository()
