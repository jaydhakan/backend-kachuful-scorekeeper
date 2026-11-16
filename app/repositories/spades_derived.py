"""Rows derived from Spades games: `spades_seats` (table mode's seats) and `spades_game_players`
(both kinds' finished games, for boards and stats)."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SpadesGamePlayer, SpadesSeat


class SpadesDerivedRepository:
    """Writes Spades' derived rows."""

    async def replace_players(
        self, session: AsyncSession, game_id: uuid.UUID, rows: Sequence[dict[str, Any]]
    ) -> None:
        """Delete and re-insert a game's `spades_game_players` rows (one executemany)."""
        await self.delete_players(session, game_id)
        if rows:
            await session.execute(insert(SpadesGamePlayer), list(rows))

    async def delete_players(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a game's `spades_game_players` rows."""
        await session.execute(delete(SpadesGamePlayer).where(SpadesGamePlayer.game_id == game_id))

    async def reseat_players(
        self, session: AsyncSession, game_id: uuid.UUID, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Point a game's rows (as player and as partner) at another registry player (merge)."""
        gp = SpadesGamePlayer
        await session.execute(
            update(gp)
            .where(gp.game_id == game_id, gp.player_id == from_id)
            .values(player_id=into_id)
        )
        await session.execute(
            update(gp)
            .where(gp.game_id == game_id, gp.partner_id == from_id)
            .values(partner_id=into_id)
        )

    async def player_ids_in_game(
        self, session: AsyncSession, game_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Registry ids with a `spades_game_players` row in this game."""
        stmt = select(SpadesGamePlayer.player_id).where(SpadesGamePlayer.game_id == game_id)
        return list((await session.execute(stmt)).scalars())

    async def replace_seats(
        self, session: AsyncSession, game_id: uuid.UUID, rows: Sequence[dict[str, Any]]
    ) -> None:
        """Delete and re-insert a table-mode game's `spades_seats` rows (one executemany)."""
        await self.delete_seats(session, game_id)
        if rows:
            await session.execute(insert(SpadesSeat), list(rows))

    async def delete_seats(self, session: AsyncSession, game_id: uuid.UUID) -> None:
        """Remove a table-mode game's `spades_seats` rows."""
        await session.execute(delete(SpadesSeat).where(SpadesSeat.game_id == game_id))

    async def seat_ids(self, session: AsyncSession, game_id: uuid.UUID) -> list[uuid.UUID]:
        """Registry ids seated in a stored table-mode game."""
        stmt = select(SpadesSeat.player_id).where(SpadesSeat.game_id == game_id)
        return list((await session.execute(stmt)).scalars())


spades_derived_repository = SpadesDerivedRepository()
