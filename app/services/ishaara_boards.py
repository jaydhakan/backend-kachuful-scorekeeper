"""Ishaara leaderboard, player stats and head-to-head over finished games; ETags as Kaali Teeri's."""

import uuid
from datetime import date, datetime, time
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ishaara.boards import head_to_head, leaderboard, tallies
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.ishaara_derived import ishaara_derived_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.ishaara import (
    IshaaraHeadToHeadOut,
    IshaaraLeaderboardOut,
    IshaaraLeaderboardRowOut,
    IshaaraPlayerStatsOut,
)
from app.services.boards import BoardsService
from app.services.cache import Cached
from app.services.kt_boards import KtPeriod, period_days
from app.services.presenters import player_summary_out
from app.settings import settings

_EMPTY_TALLY = {
    "games": 0,
    "wins": 0,
    "winRate": 0.0,
    "wordsFound": 0,
    "wrongPicks": 0,
    "trapHits": 0,
    "clueGames": 0,
    "clueWins": 0,
    "cluesGiven": 0,
    "avgClueNumber": None,
    "penalties": 0,
}


def _start_of(day: date | None) -> datetime | None:
    return None if day is None else datetime.combine(day, time(), tzinfo=settings.tz)


class IshaaraBoardsService:
    """Read-side Ishaara boards; every response carries a data-version ETag."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cache = BoardsService(session)

    async def _view(self, player_id: uuid.UUID) -> PlayerView:
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return view

    async def leaderboard(
        self, period: KtPeriod, sort: str, if_none_match: str | None
    ) -> Cached[IshaaraLeaderboardOut]:
        """`GET /api/ishaara/leaderboard`: games counted on the APP_TZ day they finished."""
        first, end = period_days(period, now_utc().astimezone(settings.tz).date())
        build = partial(self._leaderboard_body, period, sort, first, end)
        return await self._cache.cached(if_none_match, ("ishaara-board", sort, first, end), build)

    async def _leaderboard_body(
        self, period: KtPeriod, sort: str, first: date | None, end: date | None
    ) -> IshaaraLeaderboardOut:
        rows = await ishaara_derived_repository.rows_between(
            self.session, _start_of(first), _start_of(end)
        )
        board = leaderboard(rows, sort)
        views = await players_repository.get_views(
            self.session, [uuid.UUID(r["playerId"]) for r in board]
        )
        out = [
            IshaaraLeaderboardRowOut.model_validate(
                {**r, "player": player_summary_out(views[uuid.UUID(r["playerId"])])}
            )
            for r in board
        ]
        return IshaaraLeaderboardOut(
            period=period.value, sort=sort, rows=out, generated_at=to_ms(now_utc())
        )

    async def player_stats(
        self, player_id: uuid.UUID, if_none_match: str | None
    ) -> Cached[IshaaraPlayerStatsOut]:
        """`GET /api/ishaara/players/{id}/stats`, all time."""
        await self._view(player_id)
        build = partial(self._stats_body, player_id)
        return await self._cache.cached(if_none_match, ("ishaara-stats", player_id), build)

    async def _stats_body(self, player_id: uuid.UUID) -> IshaaraPlayerStatsOut:
        rows = await ishaara_derived_repository.player_rows(self.session, player_id)
        found = tallies(rows)
        return IshaaraPlayerStatsOut.model_validate(found[0] if found else _EMPTY_TALLY)

    async def head_to_head(
        self, a: uuid.UUID, b: uuid.UUID, if_none_match: str | None
    ) -> Cached[IshaaraHeadToHeadOut]:
        """`GET /api/ishaara/players/{a}/vs/{b}`, all time."""
        view_a, view_b = await self._view(a), await self._view(b)
        build = partial(self._vs_body, view_a, view_b)
        return await self._cache.cached(if_none_match, ("ishaara-vs", a, b), build)

    async def _vs_body(self, view_a: PlayerView, view_b: PlayerView) -> IshaaraHeadToHeadOut:
        a, b = view_a.player.id, view_b.player.id
        rows = await ishaara_derived_repository.shared_rows(self.session, a, b)
        counts = head_to_head(rows, str(a), str(b))
        return IshaaraHeadToHeadOut.model_validate(
            {**counts, "a": player_summary_out(view_a), "b": player_summary_out(view_b)}
        )
