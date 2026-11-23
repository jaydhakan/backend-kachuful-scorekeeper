"""Sikke entries, and the finished games and achievement rows they are computed from."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import delete, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Player, SikkeEntry
from app.domain.enums import GameKind, ProgressionGame
from app.domain.sikke import AchievementFact, FinishedGame
from app.domain.time import to_ms

UPSERT_CHUNK_ROWS = 1000
"""Rows per upsert statement: 9 columns each stays well under asyncpg's 32767 parameters."""

# Every kind's finished seats: a void game (deleted, abandoned, cancelled) has no rows here, and a
# replaced Spades seat (`result` null) didn't finish. A Spades draw is a game, not a win.
_FINISHED_GAMES = text(
    """
    SELECT s.player_id, s.game_id, g.kind, s.ended_at, s.won, g.player_count
    FROM (
        SELECT player_id, game_id, ended_at, won FROM game_players
        WHERE player_id = ANY(CAST(:player_ids AS uuid[]))
          AND ended_at IS NOT NULL AND place IS NOT NULL
        UNION ALL
        SELECT player_id, game_id, ended_at, won FROM kt_game_players
        WHERE player_id = ANY(CAST(:player_ids AS uuid[])) AND ended_at IS NOT NULL
        UNION ALL
        SELECT player_id, game_id, finished_at, won FROM ishaara_game_players
        WHERE player_id = ANY(CAST(:player_ids AS uuid[]))
        UNION ALL
        SELECT player_id, game_id, finished_at, result = 'win' FROM spades_game_players
        WHERE player_id = ANY(CAST(:player_ids AS uuid[])) AND result IS NOT NULL
    ) s
    JOIN games g ON g.id = s.game_id
    WHERE g.deleted_at IS NULL
    """
)


class StoredEntry(NamedTuple):
    """The columns a reconcile compares: thousands are read per write, so no ORM entities."""

    id: uuid.UUID
    player_id: uuid.UUID
    key: str
    kind: str
    amount: int
    game: str | None
    game_id: uuid.UUID | None
    achievement_id: uuid.UUID | None
    data: dict[str, Any] | None


_STORED = select(
    SikkeEntry.id,
    SikkeEntry.player_id,
    SikkeEntry.key,
    SikkeEntry.kind,
    SikkeEntry.amount,
    SikkeEntry.game,
    SikkeEntry.game_id,
    SikkeEntry.achievement_id,
    SikkeEntry.data,
)


async def _stored(session: AsyncSession, stmt: Any) -> list[StoredEntry]:
    return [StoredEntry(*r) for r in await session.execute(stmt)]


@dataclass(frozen=True)
class EntryRow:
    """An entry with its achievement's code and period (both null for other kinds)."""

    entry: SikkeEntry
    code: str | None
    period: str | None


def _with_codes() -> Any:
    return select(SikkeEntry, Achievement.code, Achievement.period).outerjoin(
        Achievement, Achievement.id == SikkeEntry.achievement_id
    )


class SikkeRepository:
    """Data access for `sikke_entries` and the facts Sikke is computed from."""

    async def finished_games(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[str, list[FinishedGame]]:
        """Each player's seats in finished games of every kind."""
        rows = await session.execute(_FINISHED_GAMES, {"player_ids": list(player_ids)})
        out: dict[str, list[FinishedGame]] = {}
        for player_id, game_id, kind, ended_at, won, size in rows:
            out.setdefault(str(player_id), []).append(
                FinishedGame(str(game_id), GameKind(kind), to_ms(ended_at), bool(won), size)
            )
        return out

    async def achievements(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[str, list[AchievementFact]]:
        """Each player's achievement and award rows in every game."""
        stmt = select(
            Achievement.player_id,
            Achievement.id,
            Achievement.game,
            Achievement.code,
            Achievement.game_id,
            Achievement.earned_at,
        ).where(Achievement.player_id.in_(set(player_ids)))
        out: dict[str, list[AchievementFact]] = {}
        for player_id, row_id, game, code, game_id, earned_at in await session.execute(stmt):
            out.setdefault(str(player_id), []).append(
                AchievementFact(
                    id=str(row_id),
                    game=ProgressionGame(game),
                    code=code,
                    game_id=None if game_id is None else str(game_id),
                    earned_at=to_ms(earned_at),
                )
            )
        return out

    async def for_players(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID], kinds: Iterable[str]
    ) -> list[StoredEntry]:
        """Some players' entries of some kinds."""
        return await _stored(
            session,
            _STORED.where(
                SikkeEntry.player_id.in_(set(player_ids)), SikkeEntry.kind.in_(set(kinds))
            ),
        )

    async def of_kinds(self, session: AsyncSession, kinds: Iterable[str]) -> list[StoredEntry]:
        """Everyone's entries of some kinds (bounties, the Sitara's)."""
        return await _stored(session, _STORED.where(SikkeEntry.kind.in_(set(kinds))))

    async def by_keys(self, session: AsyncSession, keys: Iterable[str]) -> list[StoredEntry]:
        """Everyone's entries with some keys (a night's Sitara pay)."""
        wanted = list(keys)
        if not wanted:
            return []
        return await _stored(session, _STORED.where(SikkeEntry.key.in_(wanted)))

    async def upsert(self, session: AsyncSession, values: Sequence[dict[str, Any]]) -> None:
        """Insert new keys and update changed ones; a kept row's `earned_at` never changes."""
        for k in range(0, len(values), UPSERT_CHUNK_ROWS):
            stmt = insert(SikkeEntry).values(list(values[k : k + UPSERT_CHUNK_ROWS]))
            stmt = stmt.on_conflict_do_update(
                index_elements=[SikkeEntry.player_id, SikkeEntry.key],
                set_={
                    c: stmt.excluded[c]
                    for c in ("kind", "amount", "game", "game_id", "achievement_id", "data")
                },
            )
            await session.execute(stmt)

    async def delete_ids(self, session: AsyncSession, ids: Iterable[uuid.UUID]) -> None:
        """Delete entries that no longer hold."""
        wanted = list(ids)
        if wanted:
            await session.execute(delete(SikkeEntry).where(SikkeEntry.id.in_(wanted)))

    async def lifetimes(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Each player's lifetime Sikke (players with no entries are missing)."""
        stmt = (
            select(SikkeEntry.player_id, func.sum(SikkeEntry.amount))
            .where(SikkeEntry.player_id.in_(set(player_ids)))
            .group_by(SikkeEntry.player_id)
        )
        return {pid: int(total) for pid, total in await session.execute(stmt)}

    async def page(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        *,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[EntryRow]:
        """One player's entries, newest first, after a keyset cursor."""
        stmt = _with_codes().where(SikkeEntry.player_id == player_id)
        if before is not None:
            stmt = stmt.where(tuple_(SikkeEntry.earned_at, SikkeEntry.id) < before)
        stmt = stmt.order_by(SikkeEntry.earned_at.desc(), SikkeEntry.id.desc()).limit(limit)
        return [EntryRow(e, code, period) for e, code, period in await session.execute(stmt)]

    async def for_game(self, session: AsyncSession, game_id: uuid.UUID) -> list[EntryRow]:
        """What one game paid: every entry whose `game_id` is it, oldest first."""
        stmt = (
            _with_codes()
            .where(SikkeEntry.game_id == game_id)
            .order_by(SikkeEntry.earned_at, SikkeEntry.id)
        )
        return [EntryRow(e, code, period) for e, code, period in await session.execute(stmt)]

    async def all_player_ids(self, session: AsyncSession) -> list[uuid.UUID]:
        """Every registry player, merged ones included (their entries must go)."""
        stmt = select(Player.id).order_by(Player.id)
        return list((await session.execute(stmt)).scalars())


sikke_repository = SikkeRepository()
