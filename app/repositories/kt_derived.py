"""Rows derived from Kaali Teeri games: kt_hands, kt_game_players and the kt_player_day rollup."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KtGamePlayer, KtHand

# One statement rebuilds every (player, day) pair given; days are APP_TZ calendar days. Every seat
# has a score in every hand, so `scores ? player` finds the hands the player sat in.
_REBUILD_DAYS = text(
    """
    INSERT INTO kt_player_day (
        player_id, day, games, wins, points, hands_bid, bids_made, partner_hands, partner_made
    )
    SELECT t.player_id, t.day, g.games, e.wins, h.points, h.hands_bid, h.bids_made,
           h.partner_hands, h.partner_made
    FROM (
        SELECT DISTINCT player_id, day,
               (day::timestamp AT TIME ZONE :tz) AS starts,
               ((day + 1)::timestamp AT TIME ZONE :tz) AS ends
        FROM unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    ) t
    CROSS JOIN LATERAL (
        SELECT count(*) AS hands,
               coalesce(sum((kh.scores ->> t.player_id::text)::int), 0) AS points,
               count(*) FILTER (WHERE kh.bidder_id = t.player_id) AS hands_bid,
               count(*) FILTER (WHERE kh.bidder_id = t.player_id AND kh.made) AS bids_made,
               count(*) FILTER (WHERE t.player_id = ANY(kh.partner_ids)) AS partner_hands,
               count(*) FILTER (WHERE t.player_id = ANY(kh.partner_ids) AND kh.made)
                   AS partner_made
        FROM kt_hands kh
        WHERE kh.done_at >= t.starts AND kh.done_at < t.ends
          AND kh.scores ? t.player_id::text
    ) h
    CROSS JOIN LATERAL (
        SELECT count(*) AS games
        FROM kt_game_players gp
        WHERE gp.player_id = t.player_id
          AND gp.first_done_at >= t.starts AND gp.first_done_at < t.ends
    ) g
    CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE gp.won) AS wins, count(*) AS ended
        FROM kt_game_players gp
        WHERE gp.player_id = t.player_id
          AND gp.ended_at >= t.starts AND gp.ended_at < t.ends
    ) e
    WHERE h.hands > 0 OR g.games > 0 OR e.ended > 0
    """
)

_DELETE_DAYS = text(
    """
    DELETE FROM kt_player_day pd
    USING unnest(CAST(:player_ids AS uuid[]), CAST(:days AS date[])) AS u(player_id, day)
    WHERE pd.player_id = u.player_id AND pd.day = u.day
    """
)


class KtDerivedRepository:
    """Writes and rebuilds Kaali Teeri's derived tables."""

    async def touched_moments(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, datetime]]:
        """(player, time) of every stored hand and game end, to rebuild their days later."""
        hands = (
            select(KtGamePlayer.player_id, KtHand.done_at)
            .join(KtHand, KtHand.game_id == KtGamePlayer.game_id)
            .where(KtGamePlayer.game_id == game_id)
        )
        ends = select(KtGamePlayer.player_id, KtGamePlayer.ended_at).where(
            KtGamePlayer.game_id == game_id, KtGamePlayer.ended_at.is_not(None)
        )
        result = await session.execute(hands.union(ends))
        return [(row[0], row[1]) for row in result]

    async def player_ids_in_game(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Registry ids seated in a stored Kaali Teeri game."""
        stmt = select(KtGamePlayer.player_id).where(KtGamePlayer.game_id == game_id)
        return list((await session.execute(stmt)).scalars())

    async def replace_rows(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        game_players: Sequence[dict[str, Any]],
        hands: Sequence[dict[str, Any]],
    ) -> None:
        """Delete and re-insert a game's derived rows (two executemany inserts)."""
        await self.delete_rows(session, game_id)
        if game_players:
            await session.execute(insert(KtGamePlayer), list(game_players))
        if hands:
            await session.execute(insert(KtHand), list(hands))

    async def delete_rows(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a game's derived rows."""
        await session.execute(delete(KtHand).where(KtHand.game_id == game_id))
        await session.execute(delete(KtGamePlayer).where(KtGamePlayer.game_id == game_id))

    async def rebuild_player_days(
        self, session: AsyncSession, pairs: set[tuple[uuid.UUID, date]], tz: str
    ) -> None:
        """Recompute `kt_player_day` for these (player, day) pairs from the two derived tables."""
        if not pairs:
            return
        params = {"player_ids": [p for p, _ in pairs], "days": [d for _, d in pairs]}
        await session.execute(_DELETE_DAYS, params)
        await session.execute(_REBUILD_DAYS, {**params, "tz": tz})


kt_derived_repository = KtDerivedRepository()
