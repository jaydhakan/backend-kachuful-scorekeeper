"""Achievements, the activity feed and closed periods."""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, ClosedPeriod
from app.domain.achievements import CareerGame, Earned, MilestoneRow
from app.domain.time import from_ms, to_ms

Key = tuple[str, str, str | None, int | None, str | None]

_CAREER_GAMES = text(
    """
    SELECT gp.player_id, gp.game_id, gp.points, gp.place, gp.won, gp.ended_at, t.worst, t.others
    FROM game_players gp
    CROSS JOIN LATERAL (
        SELECT max(o.place) AS worst, array_agg(o.player_id::text) AS others
        FROM game_players o
        WHERE o.game_id = gp.game_id AND o.place IS NOT NULL
    ) t
    WHERE gp.player_id = ANY(CAST(:player_ids AS uuid[])) AND gp.place IS NOT NULL
    ORDER BY gp.player_id, gp.ended_at, gp.game_id
    """
)

_TOTALS = text(
    """
    SELECT player_id, sum(rounds), sum(made), sum(pos_points)
    FROM player_day
    WHERE player_id = ANY(CAST(:player_ids AS uuid[]))
    GROUP BY player_id
    """
)

# Only the rounds that can carry a milestone come back; the window still orders every round.
_MILESTONE_ROUNDS = text(
    """
    SELECT game_id, round_index, done_at, outcome, points, n, made_n, positive
    FROM (
        SELECT game_id, round_index, done_at, outcome, points,
               row_number() OVER w AS n,
               count(*) FILTER (WHERE outcome = 'made') OVER w AS made_n,
               sum(greatest(points, 0)) OVER w AS positive
        FROM round_scores
        WHERE player_id = :player_id
        WINDOW w AS (ORDER BY done_at, game_id, round_index ROWS UNBOUNDED PRECEDING)
    ) x
    WHERE n IN (100, 500, 1000, 5000)
       OR (outcome = 'made' AND made_n IN (100, 500, 1000))
       OR (points > 0 AND (
            (positive >= 1000 AND positive - points < 1000)
         OR (positive >= 5000 AND positive - points < 5000)
         OR (positive >= 10000 AND positive - points < 10000)))
    """
)


def key_of(row: Achievement) -> Key:
    """A stored row's uniqueness key, in the domain's string form."""
    return (
        str(row.player_id),
        row.code,
        None if row.game_id is None else str(row.game_id),
        row.round_index,
        row.period,
    )


def _values(e: Earned) -> dict[str, Any]:
    return {
        "player_id": uuid.UUID(e.player_id),
        "code": e.code,
        "game_id": None if e.game_id is None else uuid.UUID(e.game_id),
        "round_index": e.round_index,
        "period": e.period,
        "earned_at": from_ms(e.earned_at),
        "data": e.data,
    }


class AchievementsRepository:
    """Data access for `achievements`, `activity` and `closed_periods`."""

    async def for_game(
        self, session: AsyncSession, game_id: uuid.UUID, codes: Iterable[str]
    ) -> list[Achievement]:
        """Rows a game owns among some codes."""
        stmt = select(Achievement).where(
            Achievement.game_id == game_id, Achievement.code.in_(set(codes))
        )
        return list((await session.execute(stmt)).scalars())

    async def for_players(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID], codes: Iterable[str]
    ) -> list[Achievement]:
        """Rows of some players among some codes."""
        stmt = select(Achievement).where(
            Achievement.player_id.in_(set(player_ids)), Achievement.code.in_(set(codes))
        )
        return list((await session.execute(stmt)).scalars())

    async def insert(self, session: AsyncSession, earned: Sequence[Earned]) -> list[Achievement]:
        """Insert rows (one statement) and return them."""
        if not earned:
            return []
        stmt = insert(Achievement).values([_values(e) for e in earned]).returning(Achievement)
        return list((await session.execute(stmt)).scalars())

    async def delete_ids(self, session: AsyncSession, ids: Iterable[uuid.UUID]) -> None:
        """Delete rows that no longer hold (their feed items go with them)."""
        wanted = list(ids)
        if wanted:
            await session.execute(delete(Achievement).where(Achievement.id.in_(wanted)))

    async def career_games(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[str, list[CareerGame]]:
        """Each player's ended games in (ended_at, game_id) order with table details."""
        rows = await session.execute(_CAREER_GAMES, {"player_ids": list(player_ids)})
        out: dict[str, list[CareerGame]] = {}
        for player_id, game_id, points, place, won, ended_at, worst, others in rows:
            out.setdefault(str(player_id), []).append(
                CareerGame(
                    player_id=str(player_id),
                    game_id=str(game_id),
                    points=points,
                    place=place,
                    won=won,
                    ended_at=to_ms(ended_at),
                    worst_place=worst,
                    others=tuple(others),
                )
            )
        return out

    async def totals(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[str, tuple[int, int, int]]:
        """(rounds, made, positive points) per player, from player_day."""
        rows = await session.execute(_TOTALS, {"player_ids": list(player_ids)})
        return {str(p): (int(r), int(m), int(pos)) for p, r, m, pos in rows}

    async def milestone_rounds(
        self, session: AsyncSession, player_id: uuid.UUID
    ) -> list[MilestoneRow]:
        """The rounds where a player crossed a round/made/points milestone."""
        rows = await session.execute(_MILESTONE_ROUNDS, {"player_id": player_id})
        return [
            MilestoneRow(
                player_id=str(player_id),
                game_id=str(game_id),
                round_index=round_index,
                done_at=to_ms(done_at),
                outcome=outcome,
                points=points,
                rounds_so_far=int(n),
                made_so_far=int(made_n),
                positive_so_far=int(positive),
            )
            for game_id, round_index, done_at, outcome, points, n, made_n, positive in rows
        ]

    async def add_activity(self, session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
        """Append feed items (one statement)."""
        if rows:
            await session.execute(insert(Activity), list(rows))

    async def activity_page(
        self, session: AsyncSession, *, before: tuple[datetime, int] | None, limit: int
    ) -> list[Activity]:
        """The feed, newest first."""
        stmt = select(Activity).order_by(Activity.created_at.desc(), Activity.id.desc())
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(
                    Activity.created_at < at, and_(Activity.created_at == at, Activity.id < last_id)
                )
            )
        return list((await session.execute(stmt.limit(limit))).scalars())

    async def delete_activity(self, session: AsyncSession, game_id: uuid.UUID, kind: str) -> None:
        """Remove a game's feed items of one kind."""
        await session.execute(
            delete(Activity).where(Activity.game_id == game_id, Activity.kind == kind)
        )

    async def list_page(
        self,
        session: AsyncSession,
        *,
        player_id: uuid.UUID | None,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[Achievement]:
        """Newest first, optionally for one player."""
        stmt = select(Achievement).order_by(Achievement.earned_at.desc(), Achievement.id.desc())
        if player_id is not None:
            stmt = stmt.where(Achievement.player_id == player_id)
        if before is not None:
            at, last_id = before
            stmt = stmt.where(
                or_(
                    Achievement.earned_at < at,
                    and_(Achievement.earned_at == at, Achievement.id < last_id),
                )
            )
        return list((await session.execute(stmt.limit(limit))).scalars())

    async def of_player(self, session: AsyncSession, player_id: uuid.UUID) -> list[Achievement]:
        """Every row of one player, newest first."""
        stmt = (
            select(Achievement)
            .where(Achievement.player_id == player_id)
            .order_by(Achievement.earned_at.desc(), Achievement.id.desc())
        )
        return list((await session.execute(stmt)).scalars())

    async def of_game(self, session: AsyncSession, game_id: uuid.UUID) -> list[Achievement]:
        """Every row tied to one game, oldest first."""
        stmt = (
            select(Achievement)
            .where(Achievement.game_id == game_id)
            .order_by(Achievement.earned_at, Achievement.code)
        )
        return list((await session.execute(stmt)).scalars())

    async def of_period(self, session: AsyncSession, period: str) -> list[Achievement]:
        """Awards stored for a closed day or season."""
        stmt = select(Achievement).where(Achievement.period == period).order_by(Achievement.code)
        return list((await session.execute(stmt)).scalars())

    async def move_player(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Merge: move period awards that don't clash, drop the rest of `from_id`'s rows."""
        target = select(
            Achievement.code, Achievement.game_id, Achievement.round_index, Achievement.period
        ).where(Achievement.player_id == into_id)
        clashes = tuple_(
            Achievement.code, Achievement.game_id, Achievement.round_index, Achievement.period
        ).in_(target)
        await session.execute(
            update(Achievement)
            .where(Achievement.player_id == from_id, Achievement.period.is_not(None), ~clashes)
            .values(player_id=into_id)
        )
        await session.execute(delete(Achievement).where(Achievement.player_id == from_id))

    async def closed(self, session: AsyncSession, period: str) -> ClosedPeriod | None:
        """A finalised day or season."""
        return await session.get(ClosedPeriod, period)

    async def close(
        self, session: AsyncSession, period: str, kind: str, data: dict[str, Any]
    ) -> None:
        """Freeze a day or season."""
        session.add(ClosedPeriod(period=period, kind=kind, data=data))
        await session.flush()

    async def closed_seasons(self, session: AsyncSession) -> list[ClosedPeriod]:
        """Every closed season, newest first."""
        stmt = (
            select(ClosedPeriod)
            .where(ClosedPeriod.kind == "season")
            .order_by(ClosedPeriod.period.desc())
        )
        return list((await session.execute(stmt)).scalars())


achievements_repository = AchievementsRepository()
