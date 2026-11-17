"""What Kaali Teeri's achievements and awards read: kt_hands and kt_game_players rows as facts.

SQL only, no rules; a fixed number of queries per call, never `games.payload`.
`month_tallies` and `first_finished` feed the win-rate seasons.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KtGamePlayer, KtHand
from app.domain.kaali_teeri.achievements import (
    KtCareerFacts,
    KtGameFacts,
    KtHandFact,
    KtSeatFact,
    deck_of,
)
from app.domain.kaali_teeri.awards import KtDayFacts, KtMonthFacts
from app.domain.kaali_teeri.game import Sweep
from app.domain.progression import WinTally
from app.domain.time import from_ms, to_ms, to_ms_or_none


def _hand_fact(row: KtHand) -> KtHandFact:
    return KtHandFact(
        game_id=str(row.game_id),
        hand_index=row.hand_index,
        done_at=to_ms(row.done_at),
        deck=deck_of(len(row.scores)),
        bidder_id=str(row.bidder_id),
        bid=row.bid,
        forced=row.forced,
        partner_ids=tuple(str(p) for p in row.partner_ids),
        slots=dict(row.slots),
        team_points=row.team_points,
        made=row.made,
        sweep=Sweep(row.sweep),
        scores=dict(row.scores),
    )


def _seat_fact(row: KtGamePlayer) -> KtSeatFact:
    return KtSeatFact(
        game_id=str(row.game_id),
        player_id=str(row.player_id),
        points=row.points,
        place=row.place,
        ended_at=to_ms_or_none(row.ended_at),
        hands_bid=row.hands_bid,
        bids_made=row.bids_made,
        partner_made=row.partner_made,
    )


def _hands(rows: Iterable[KtHand]) -> tuple[KtHandFact, ...]:
    return tuple(_hand_fact(r) for r in rows)


def _window(start_ms: int, end_ms: int) -> Select[KtHand]:
    """Hands done in [start, end), in play order."""
    return (
        select(KtHand)
        .where(KtHand.done_at >= from_ms(start_ms), KtHand.done_at < from_ms(end_ms))
        .order_by(KtHand.done_at, KtHand.game_id, KtHand.hand_index)
    )


class KtProgressionRepository:
    """Facts for Kaali Teeri's achievements, daily awards and seasons."""

    async def game_facts(self, session: AsyncSession, game_id: uuid.UUID) -> KtGameFacts | None:
        """One stored game's hands and seats (two queries); None when it has no rows."""
        seats = (
            await session.scalars(
                select(KtGamePlayer)
                .where(KtGamePlayer.game_id == game_id)
                .order_by(KtGamePlayer.seat)
            )
        ).all()
        if not seats:
            return None
        hands = await session.scalars(
            select(KtHand).where(KtHand.game_id == game_id).order_by(KtHand.hand_index)
        )
        return KtGameFacts(
            game_id=str(game_id),
            hands=_hands(hands),
            seats=tuple(_seat_fact(s) for s in seats),
        )

    async def career_facts(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> KtCareerFacts:
        """Every seat and hand of every game these players sat in (two queries)."""
        ids = tuple(sorted(str(p) for p in player_ids))
        if not player_ids:
            return KtCareerFacts(player_ids=ids)
        games = (
            select(KtGamePlayer.game_id)
            .where(KtGamePlayer.player_id.in_(player_ids))
            .scalar_subquery()
        )
        seats = await session.scalars(
            select(KtGamePlayer)
            .where(KtGamePlayer.game_id.in_(games))
            .order_by(KtGamePlayer.game_id, KtGamePlayer.seat)
        )
        seat_facts = tuple(_seat_fact(s) for s in seats)
        hands = await session.scalars(
            select(KtHand)
            .where(KtHand.game_id.in_(games))
            .order_by(KtHand.done_at, KtHand.game_id, KtHand.hand_index)
        )
        return KtCareerFacts(player_ids=ids, hands=_hands(hands), seats=seat_facts)

    async def day_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> KtDayFacts:
        """The hands done in a day (one query)."""
        hands = await session.scalars(_window(start_ms, end_ms))
        return KtDayFacts(start_ms=start_ms, end_ms=end_ms, hands=_hands(hands))

    async def month_facts(self, session: AsyncSession, start_ms: int, end_ms: int) -> KtMonthFacts:
        """The hands done in a month, for the season's own awards (one query)."""
        hands = await session.scalars(_window(start_ms, end_ms))
        return KtMonthFacts(start_ms=start_ms, end_ms=end_ms, hands=_hands(hands))

    async def month_tallies(
        self, session: AsyncSession, start_ms: int, end_ms: int
    ) -> list[WinTally]:
        """Games and wins per player that finished in [start, end) (one query)."""
        # Ended games (place set; a tie for first is a win for each).
        stmt = (
            select(
                KtGamePlayer.player_id,
                func.count(),
                func.count().filter(KtGamePlayer.won),
            )
            .where(
                KtGamePlayer.ended_at >= from_ms(start_ms),
                KtGamePlayer.ended_at < from_ms(end_ms),
                KtGamePlayer.place.is_not(None),
            )
            .group_by(KtGamePlayer.player_id)
        )
        rows = await session.execute(stmt)
        return [WinTally(str(p), int(games), int(wins)) for p, games, wins in rows]

    async def first_finished(self, session: AsyncSession) -> datetime | None:
        """When the first game finished, if any."""
        stmt = select(func.min(KtGamePlayer.ended_at)).where(KtGamePlayer.place.is_not(None))
        return (await session.execute(stmt)).scalar_one_or_none()


kt_progression_repository = KtProgressionRepository()
