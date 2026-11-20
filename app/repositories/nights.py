"""Nights, and the finished games of every kind they are grouped from."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, Game, Night, Player, SikkeEntry
from app.domain.time import to_ms

# Every kind's finished seats, as Sikke counts them: a void game has no rows here.
_SEATS = """
    SELECT s.game_id, g.kind, g.created_at, s.ended_at, s.player_id, s.won
    FROM (
        SELECT game_id, player_id, ended_at, won FROM game_players
        WHERE ended_at IS NOT NULL AND place IS NOT NULL
        UNION ALL
        SELECT game_id, player_id, ended_at, won FROM kt_game_players WHERE ended_at IS NOT NULL
        UNION ALL
        SELECT game_id, player_id, finished_at, won FROM ishaara_game_players
        UNION ALL
        SELECT game_id, player_id, finished_at, result = 'win' FROM spades_game_players
        WHERE result IS NOT NULL
    ) s
    JOIN games g ON g.id = s.game_id
    WHERE g.deleted_at IS NULL
"""

# The queries below only join constant SQL; every value is a bound parameter.
_SEATS_OF = text(
    f"SELECT * FROM ({_SEATS}) f WHERE f.game_id = ANY(CAST(:ids AS uuid[]))"  # noqa: S608
)

# Games sharing a player with a set of games, inside the time span a link could reach.
_NEAR = text(
    f"""
    WITH seats AS ({_SEATS}),
    near AS (
        SELECT DISTINCT game_id FROM seats
        WHERE player_id = ANY(CAST(:players AS uuid[]))
          AND created_at <= :until AND ended_at >= :since
    )
    SELECT seats.* FROM seats JOIN near USING (game_id)
    """  # noqa: S608
)

# Finished games no stored night holds yet, ended inside a window.
_UNCOVERED = text(
    f"""
    SELECT DISTINCT f.game_id FROM ({_SEATS}) f
    WHERE f.ended_at <= :until AND (CAST(:since AS timestamptz) IS NULL OR f.ended_at >= :since)
      AND (CAST(:player AS uuid) IS NULL OR f.player_id = :player)
      AND NOT EXISTS (SELECT 1 FROM nights n WHERE n.games @> ARRAY[f.game_id])
    """  # noqa: S608
)


@dataclass(frozen=True)
class SeatRow:
    """One seat of a finished game of any kind."""

    game_id: str
    kind: str
    created_at: int
    ended_at: int
    player_id: str
    won: bool


def _seats(rows: Iterable[Sequence[Any]]) -> list[SeatRow]:
    return [
        SeatRow(str(gid), kind, to_ms(created), to_ms(ended), str(pid), bool(won))
        for gid, kind, created, ended, pid, won in rows
    ]


class NightsRepository:
    """Data access for `nights` and the facts a night is built from."""

    async def seats_of(self, session: AsyncSession, game_ids: Iterable[str]) -> list[SeatRow]:
        """The seats of some games, if they are finished."""
        return _seats(await session.execute(_SEATS_OF, {"ids": [uuid.UUID(g) for g in game_ids]}))

    async def seats_near(
        self, session: AsyncSession, players: Iterable[str], since: datetime, until: datetime
    ) -> list[SeatRow]:
        """Every seat of finished games that seat one of `players` between `since` and `until`."""
        params = {"players": [uuid.UUID(p) for p in players], "since": since, "until": until}
        return _seats(await session.execute(_NEAR, params))

    async def uncovered(
        self,
        session: AsyncSession,
        *,
        until: datetime,
        since: datetime | None,
        player_id: uuid.UUID | None = None,
    ) -> list[str]:
        """Finished games ended in [since, until] that no stored night holds (one player's)."""
        params = {"until": until, "since": since, "player": player_id}
        return [str(g) for g in (await session.execute(_UNCOVERED, params)).scalars()]

    async def overlapping(self, session: AsyncSession, game_ids: Iterable[str]) -> list[Night]:
        """Stored nights holding any of some games."""
        ids = [uuid.UUID(g) for g in game_ids]
        if not ids:
            return []
        stmt = select(Night).where(Night.games.overlap(ids)).order_by(Night.id)
        # Nights are written with Core upserts: never trust an object loaded earlier.
        stmt = stmt.execution_options(populate_existing=True)
        return list((await session.execute(stmt)).scalars())

    async def get(self, session: AsyncSession, night_id: uuid.UUID) -> Night | None:
        """One stored night."""
        return await session.get(Night, night_id, populate_existing=True)

    async def upsert(self, session: AsyncSession, values: Sequence[dict[str, Any]]) -> None:
        """Write nights by id; a kept night keeps its `closed_at`."""
        if not values:
            return
        stmt = insert(Night).values(list(values))
        stmt = stmt.on_conflict_do_update(
            index_elements=[Night.id],
            set_={
                c: stmt.excluded[c] for c in ("started_at", "ended_at", "players", "games", "data")
            },
        )
        await session.execute(stmt)

    async def delete(self, session: AsyncSession, ids: Iterable[uuid.UUID]) -> None:
        """Delete nights that no longer exist (their games went or joined another night)."""
        wanted = list(ids)
        if wanted:
            await session.execute(delete(Night).where(Night.id.in_(wanted)))

    async def page(
        self,
        session: AsyncSession,
        player_ids: Sequence[uuid.UUID],
        *,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[Night]:
        """Closed nights of a player (any of their ids), newest first."""
        stmt = select(Night).where(
            Night.players.overlap(list(player_ids)), Night.closed_at.is_not(None)
        )
        if before is not None:
            stmt = stmt.where(tuple_(Night.ended_at, Night.id) < before)
        stmt = stmt.order_by(Night.ended_at.desc(), Night.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    async def merged_into(self, session: AsyncSession, player_id: uuid.UUID) -> list[uuid.UUID]:
        """Players merged into this one (their old nights still carry those ids)."""
        stmt = select(Player.id).where(Player.merged_into == player_id).order_by(Player.id)
        return list((await session.execute(stmt)).scalars())

    async def kachuful_payloads(
        self, session: AsyncSession, game_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Some Kachuful games' stored payloads (a night's story reads its rounds)."""
        stmt = select(Game.payload).where(
            Game.id.in_([uuid.UUID(g) for g in game_ids]), Game.kind == "kachuful"
        )
        return list((await session.execute(stmt)).scalars())

    async def achievements(
        self, session: AsyncSession, game_ids: Iterable[str]
    ) -> list[tuple[str, str, str]]:
        """(player, game, code) earned in some games, oldest first."""
        stmt = (
            select(Achievement.player_id, Achievement.game, Achievement.code)
            .where(Achievement.game_id.in_([uuid.UUID(g) for g in game_ids]))
            .order_by(Achievement.earned_at, Achievement.id)
        )
        return [(str(p), g, c) for p, g, c in await session.execute(stmt)]

    async def sikke(
        self, session: AsyncSession, game_ids: Iterable[str], keys: Iterable[str]
    ) -> dict[str, int]:
        """Each player's Sikke from some games' entries plus some keyed entries (the Sitara's)."""
        stmt = (
            select(SikkeEntry.player_id, func.sum(SikkeEntry.amount))
            .where(
                SikkeEntry.game_id.in_([uuid.UUID(g) for g in game_ids])
                | SikkeEntry.key.in_(list(keys))
            )
            .group_by(SikkeEntry.player_id)
            .order_by(SikkeEntry.player_id)
        )
        return {str(p): int(n) for p, n in await session.execute(stmt)}

    async def activity(self, session: AsyncSession, night_ids: Iterable[str]) -> list[Activity]:
        """The `night` feed items of some nights, oldest first."""
        stmt = (
            select(Activity)
            .where(Activity.kind == "night", Activity.data["nightId"].astext.in_(list(night_ids)))
            .order_by(Activity.id)
        )
        return list((await session.execute(stmt)).scalars())

    async def set_activity(
        self, session: AsyncSession, activity_id: int, *, player_id: uuid.UUID | None, data: Any
    ) -> None:
        """Point a night's feed item at its recomputed night."""
        await session.execute(
            update(Activity)
            .where(Activity.id == activity_id)
            .values(player_id=player_id, data=data)
        )

    async def delete_activity(self, session: AsyncSession, ids: Iterable[int]) -> None:
        """Drop feed items of nights that are gone."""
        wanted = list(ids)
        if wanted:
            await session.execute(delete(Activity).where(Activity.id.in_(wanted)))

    async def all_sitaras(self, session: AsyncSession) -> list[tuple[str, datetime, list[str]]]:
        """Every closed night's id, end and Sitara (recompute-sikke)."""
        stmt = select(Night.id, Night.ended_at, Night.data["sitara"]).where(
            Night.closed_at.is_not(None)
        )
        return [(str(i), at, list(s or [])) for i, at, s in await session.execute(stmt)]


nights_repository = NightsRepository()
