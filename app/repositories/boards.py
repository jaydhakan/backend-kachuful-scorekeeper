"""Board and stats queries. Whole days read player_day; only partial edge hours read round_scores."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.leaderboard import Tally, merge_tallies

# One round trip: four parts per player, merged in Python. {filter} is a constant clause.
_TALLY_SQL = """
WITH days AS (
    SELECT player_id, sum(rounds) AS rounds, sum(points) AS points, sum(made) AS made,
           max(best_round) AS best_round, min(worst_round) AS worst_round,
           max(max_hot) AS hot, max(max_cold) AS cold, sum(hook_hits) AS hook_hits
    FROM player_day
    WHERE day >= :first_day AND day < :end_day {filter}
    GROUP BY player_id
), edges AS (
    SELECT player_id, count(*) AS rounds, sum(points) AS points,
           count(*) FILTER (WHERE outcome = 'made') AS made,
           max(points) AS best_round, min(points) AS worst_round,
           max(run_hot) AS hot, max(run_cold) AS cold, sum(hook_hits) AS hook_hits
    FROM round_scores
    WHERE ((done_at >= :edge1_from AND done_at < :edge1_to)
        OR (done_at >= :edge2_from AND done_at < :edge2_to)) {filter}
    GROUP BY player_id
), played AS (
    SELECT player_id, count(*) AS games
    FROM game_players
    WHERE last_done_at >= :range_from AND first_done_at < :range_to {filter}
    GROUP BY player_id
), ended AS (
    SELECT player_id, count(*) AS games_ended, count(*) FILTER (WHERE won) AS wins
    FROM game_players
    WHERE ended_at >= :range_from AND ended_at < :range_to AND place IS NOT NULL {filter}
    GROUP BY player_id
)
SELECT player_id, rounds, points, made, best_round, worst_round, hot, cold, hook_hits,
       0 AS games, 0 AS games_ended, 0 AS wins FROM days
UNION ALL
SELECT player_id, rounds, points, made, best_round, worst_round, hot, cold, hook_hits,
       0, 0, 0 FROM edges
UNION ALL
SELECT player_id, 0, 0, 0, NULL, NULL, 0, 0, 0, games, 0, 0 FROM played
UNION ALL
SELECT player_id, 0, 0, 0, NULL, NULL, 0, 0, 0, 0, games_ended, wins FROM ended
"""

_ALL_PLAYERS = text(_TALLY_SQL.format(filter=""))
_ONE_PLAYER = text(_TALLY_SQL.format(filter="AND player_id = :player_id"))

_BREAKDOWN = text(
    """
    SELECT bid, cards, trump, is_dealer, count(*) AS total,
           count(*) FILTER (WHERE outcome = 'made') AS made,
           GROUPING(bid) AS g_bid, GROUPING(cards) AS g_cards,
           GROUPING(trump) AS g_trump, GROUPING(is_dealer) AS g_dealer
    FROM round_scores
    WHERE player_id = :player_id AND done_at >= :range_from AND done_at < :range_to
    GROUP BY GROUPING SETS ((bid), (cards), (trump), (is_dealer))
    """
)


@dataclass(frozen=True)
class Window:
    """A half-open range split into whole APP_TZ days and the edge hours around them."""

    range_from: datetime
    range_to: datetime
    first_day: date
    end_day: date
    edge1: tuple[datetime, datetime]
    edge2: tuple[datetime, datetime]


@dataclass(frozen=True)
class Breakdown:
    """made/total per bid, cards and trump, and as dealer."""

    by_bid: dict[str, tuple[int, int]]
    by_cards: dict[str, tuple[int, int]]
    by_trump: dict[str, tuple[int, int]]
    as_dealer: tuple[int, int]


@dataclass(frozen=True)
class HeadToHeadGame:
    """A game both players finished."""

    game_id: uuid.UUID
    a_points: int
    a_place: int
    b_points: int
    b_place: int


def _tally(r: Sequence[Any]) -> Tally:
    return Tally(
        player_id=str(r[0]),
        rounds=int(r[1]),
        points=int(r[2]),
        made=int(r[3]),
        best_round=float("-inf") if r[4] is None else int(r[4]),
        worst_round=float("inf") if r[5] is None else int(r[5]),
        longest_hot=int(r[6] or 0),
        longest_cold=int(r[7] or 0),
        hook_hits=int(r[8] or 0),
        games=int(r[9]),
        games_ended=int(r[10]),
        wins=int(r[11]),
    )


def _params(window: Window) -> dict[str, object]:
    return {
        "first_day": window.first_day,
        "end_day": window.end_day,
        "edge1_from": window.edge1[0],
        "edge1_to": window.edge1[1],
        "edge2_from": window.edge2[0],
        "edge2_to": window.edge2[1],
        "range_from": window.range_from,
        "range_to": window.range_to,
    }


class BoardsRepository:
    """Aggregates for boards, stats, calendars and head-to-head."""

    async def tallies(
        self, session: AsyncSession, window: Window, player_id: uuid.UUID | None = None
    ) -> dict[str, Tally]:
        """Every player's (or one player's) tally over a window, in one query."""
        if player_id is None:
            result = await session.execute(_ALL_PLAYERS, _params(window))
        else:
            result = await session.execute(_ONE_PLAYER, {**_params(window), "player_id": player_id})
        out: dict[str, Tally] = {}
        for row in result:
            part = _tally(row)
            out[part.player_id] = merge_tallies(
                out.get(part.player_id, Tally(part.player_id)), part
            )
        return out

    async def breakdown(
        self, session: AsyncSession, player_id: uuid.UUID, window: Window
    ) -> Breakdown:
        """made/total by bid, cards, trump and dealer seat (one GROUPING SETS query)."""
        rows = await session.execute(
            _BREAKDOWN,
            {"player_id": player_id, "range_from": window.range_from, "range_to": window.range_to},
        )
        by_bid: dict[str, tuple[int, int]] = {}
        by_cards: dict[str, tuple[int, int]] = {}
        by_trump: dict[str, tuple[int, int]] = {}
        dealer = (0, 0)
        for bid, cards, trump, is_dealer, total, made, g_bid, g_cards, g_trump, _ in rows:
            if not g_bid:
                by_bid[str(bid)] = (made, total)
            elif not g_cards:
                by_cards[str(cards)] = (made, total)
            elif not g_trump:
                by_trump[str(trump)] = (made, total)
            elif is_dealer:
                dealer = (made, total)
        return Breakdown(by_bid=by_bid, by_cards=by_cards, by_trump=by_trump, as_dealer=dealer)

    async def places(
        self, session: AsyncSession, player_id: uuid.UUID, window: Window
    ) -> tuple[dict[int, int], uuid.UUID | None]:
        """Finishes in 1st/2nd/3rd, and the best-scoring ended game (earliest wins a tie)."""
        stmt = text(
            """
            SELECT gp.game_id, gp.points, gp.place
            FROM game_players gp JOIN games g ON g.id = gp.game_id
            WHERE gp.player_id = :player_id AND gp.place IS NOT NULL
              AND gp.ended_at >= :range_from AND gp.ended_at < :range_to
            ORDER BY gp.points DESC, g.created_at, gp.game_id
            """
        )
        rows = (
            await session.execute(
                stmt,
                {
                    "player_id": player_id,
                    "range_from": window.range_from,
                    "range_to": window.range_to,
                },
            )
        ).all()
        counts = {1: 0, 2: 0, 3: 0}
        for _, _, place in rows:
            if place in counts:
                counts[place] += 1
        return counts, rows[0][0] if rows else None

    async def calendar(
        self, session: AsyncSession, player_id: uuid.UUID, start: date, end: date
    ) -> dict[str, int]:
        """Rounds per APP_TZ day for a player."""
        stmt = text(
            "SELECT day, rounds FROM player_day "
            "WHERE player_id = :player_id AND day >= :start AND day < :end AND rounds > 0 "
            "ORDER BY day"
        )
        rows = await session.execute(stmt, {"player_id": player_id, "start": start, "end": end})
        return {day.isoformat(): int(rounds) for day, rounds in rows}

    async def head_to_head(
        self, session: AsyncSession, a: uuid.UUID, b: uuid.UUID, window: Window
    ) -> list[HeadToHeadGame]:
        """Games both players finished in the window, newest end first."""
        stmt = text(
            """
            SELECT ga.game_id, ga.points, ga.place, gb.points, gb.place
            FROM game_players ga JOIN game_players gb ON gb.game_id = ga.game_id
            WHERE ga.player_id = :a AND gb.player_id = :b
              AND ga.place IS NOT NULL AND gb.place IS NOT NULL
              AND ga.ended_at >= :range_from AND ga.ended_at < :range_to
            ORDER BY ga.ended_at DESC, ga.game_id
            """
        )
        rows = await session.execute(
            stmt, {"a": a, "b": b, "range_from": window.range_from, "range_to": window.range_to}
        )
        return [HeadToHeadGame(*row) for row in rows]

    async def player_game_ids(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        window: Window,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[tuple[uuid.UUID, datetime]]:
        """A player's games with a round in the window, newest created first."""
        stmt = text(
            """
            SELECT g.id, g.created_at
            FROM game_players gp JOIN games g ON g.id = gp.game_id
            WHERE gp.player_id = :player_id AND g.deleted_at IS NULL
              AND gp.last_done_at >= :range_from AND gp.first_done_at < :range_to
              AND (CAST(:before_at AS timestamptz) IS NULL
                   OR (g.created_at, g.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid)))
            ORDER BY g.created_at DESC, g.id DESC
            LIMIT :limit
            """
        )
        rows = await session.execute(
            stmt,
            {
                "player_id": player_id,
                "range_from": window.range_from,
                "range_to": window.range_to,
                "before_at": before[0] if before else None,
                "before_id": before[1] if before else None,
                "limit": limit,
            },
        )
        return [(row[0], row[1]) for row in rows]


boards_repository = BoardsRepository()
