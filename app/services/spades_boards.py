"""Spades leaderboard, player stats, head-to-head, player games and bootstrap over both kinds.

Boards read `spades_game_players`: a draw is a game played, not won; a cancelled game has no rows;
a replaced player's rows (no result) count their hands but no game. ETags as Kaali Teeri's.
"""

import uuid
from collections import defaultdict
from datetime import date, datetime, time
from enum import StrEnum
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import GameKind
from app.domain.spades.stats import (
    SpGameFact,
    SpTotals,
    head_to_head,
    pairs,
    partners_of,
    rank_board,
    rates,
    total_facts,
)
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.repositories.players import PlayerView, players_repository
from app.repositories.spades_boards import spades_boards_repository
from app.schemas.common import Page
from app.schemas.spades import (
    SpBootstrapOut,
    SpGameSummaryOut,
    SpHeadToHeadOut,
    SpLeaderboardOut,
    SpLeaderboardRowOut,
    SpPairOut,
    SpPartnerOut,
    SpPlayerStatsOut,
)
from app.schemas.spades_tables import SpTableSummaryOut
from app.services.boards import BoardsService
from app.services.cache import Cached, etag_for, etag_matches
from app.services.kt_boards import KtPeriod, period_days
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_summary_out, sp_summary_out
from app.services.spades_state import table_summary_out
from app.settings import settings

TOP_PAIRS = 20

SpGameOrTable = SpGameSummaryOut | SpTableSummaryOut


class SpSort(StrEnum):
    """Leaderboard order: games won, win rate (3+ games), or bids made / bids."""

    WINS = "wins"
    RATE = "rate"
    MADE = "made"


def _start_of(day: date | None) -> datetime | None:
    return None if day is None else datetime.combine(day, time(), tzinfo=settings.tz)


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


def _totals_fields(t: SpTotals) -> dict[str, object]:
    """The shared totals block of a board row and a player's stats (snake_case)."""
    s = t.tally
    return {
        "games": t.games,
        "wins": t.wins,
        "draws": t.draws,
        "losses": t.losses,
        "hands": s.hands,
        "bids": s.bids,
        "bids_made": s.bids_made,
        "nils": s.nils,
        "nils_made": s.nils_made,
        "blind_nils": s.blind_nils,
        "blind_nils_made": s.blind_nils_made,
        "bags": s.bags,
        "sets": s.sets,
        **rates(t),
    }


def _by_player(facts: list[SpGameFact]) -> dict[str, SpTotals]:
    grouped: dict[str, list[SpGameFact]] = defaultdict(list)
    for f in facts:
        grouped[f.player_id].append(f)
    return {pid: total_facts(rows) for pid, rows in grouped.items()}


class SpadesBoardsService:
    """Read-side Spades boards and stats; every response carries a data-version ETag."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cache = BoardsService(session)

    async def _view(self, player_id: uuid.UUID) -> PlayerView:
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return view

    async def leaderboard(
        self, period: KtPeriod, sort: SpSort, if_none_match: str | None
    ) -> Cached[SpLeaderboardOut]:
        """`GET /api/spades/leaderboard`: games counted on the APP_TZ day they finished."""
        first, end = period_days(period, now_utc().astimezone(settings.tz).date())
        build = partial(self._leaderboard_body, period, sort, first, end)
        return await self._cache.cached(if_none_match, ("sp-board", sort, first, end), build)

    async def _leaderboard_body(
        self, period: KtPeriod, sort: SpSort, first: date | None, end: date | None
    ) -> SpLeaderboardOut:
        facts = await spades_boards_repository.facts_between(
            self.session, _start_of(first), _start_of(end)
        )
        board = rank_board(_by_player(facts), sort.value)
        best = pairs(facts)[:TOP_PAIRS]
        ids = {r.player_id for r in board} | {p for x in best for p in (x.a, x.b)}
        views = await players_repository.get_views(self.session, [uuid.UUID(i) for i in ids])
        rows = [
            SpLeaderboardRowOut.model_validate(
                {
                    **_totals_fields(r.totals),
                    "rank": r.rank,
                    "player": player_summary_out(views[uuid.UUID(r.player_id)]),
                    "value": r.value,
                }
            )
            for r in board
        ]
        pair_rows = [
            SpPairOut(
                a=player_summary_out(views[uuid.UUID(x.a)]),
                b=player_summary_out(views[uuid.UUID(x.b)]),
                games=x.games,
                wins=x.wins,
                draws=x.draws,
                win_rate=x.wins / x.games,
            )
            for x in best
        ]
        return SpLeaderboardOut(
            period=period.value,
            sort=sort.value,
            rows=rows,
            pairs=pair_rows,
            generated_at=to_ms(now_utc()),
        )

    async def player_stats(
        self, player_id: uuid.UUID, if_none_match: str | None
    ) -> Cached[SpPlayerStatsOut]:
        """`GET /api/spades/players/{id}/stats`, all time."""
        await self._view(player_id)
        build = partial(self._stats_body, player_id)
        return await self._cache.cached(if_none_match, ("sp-stats", player_id), build)

    async def _stats_body(self, player_id: uuid.UUID) -> SpPlayerStatsOut:
        facts = await spades_boards_repository.player_facts(self.session, [player_id])
        totals = total_facts(facts)
        partners = [
            SpPartnerOut(
                player_id=p.player_id, games=p.games, wins=p.wins, win_rate=p.wins / p.games
            )
            for p in partners_of(facts, str(player_id))
        ]
        return SpPlayerStatsOut.model_validate(
            {**_totals_fields(totals), "best_score": totals.best_score, "partners": partners}
        )

    async def head_to_head(
        self, a: uuid.UUID, b: uuid.UUID, if_none_match: str | None
    ) -> Cached[SpHeadToHeadOut]:
        """`GET /api/spades/players/{a}/vs/{b}`, all time."""
        view_a, view_b = await self._view(a), await self._view(b)
        build = partial(self._vs_body, view_a, view_b)
        return await self._cache.cached(if_none_match, ("sp-vs", a, b), build)

    async def _vs_body(self, view_a: PlayerView, view_b: PlayerView) -> SpHeadToHeadOut:
        a, b = view_a.player.id, view_b.player.id
        facts = await spades_boards_repository.player_facts(self.session, [a, b])
        counts = head_to_head(facts, str(a), str(b))
        return SpHeadToHeadOut(
            a=player_summary_out(view_a),
            b=player_summary_out(view_b),
            games=counts.games,
            together=counts.together,
            together_wins=counts.together_wins,
            against=counts.against,
            a_wins=counts.a_wins,
            b_wins=counts.b_wins,
            draws=counts.draws,
        )

    async def player_games(
        self, player_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> Page[SpGameOrTable]:
        """`GET /api/spades/players/{id}/games`: both kinds, newest created first."""
        await self._view(player_id)
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            before = (at, _cursor_id(key))
        found = await spades_boards_repository.player_game_ids(
            self.session, player_id, before, limit + 1
        )
        page = found[:limit]
        games = await games_repository.summaries(
            self.session, [i for i, k, _ in page if k == GameKind.SPADES], kind=GameKind.SPADES
        )
        tables = await games_repository.summaries(
            self.session,
            [i for i, k, _ in page if k == GameKind.SPADES_ONLINE],
            kind=GameKind.SPADES_ONLINE,
        )
        items: list[SpGameOrTable] = [
            sp_summary_out(games[gid])
            if kind == GameKind.SPADES
            else table_summary_out(gid, tables[gid].summary, player_id=player_id, is_host=False)
            for gid, kind, _ in page
        ]
        next_cursor = None
        if len(found) > limit:
            last_id, _, last_at = found[limit - 1]
            next_cursor = encode_cursor(last_at, str(last_id))
        return Page[SpGameOrTable](items=items, next_cursor=next_cursor)

    async def bootstrap(self, user: User, if_none_match: str | None) -> Cached[SpBootstrapOut]:
        """`GET /api/spades/bootstrap`: the caller's live table-mode games and the data version."""
        data_version = await app_state_repository.data_version(self.session)
        etag = etag_for(data_version, "sp-bootstrap", user.id)
        if etag_matches(if_none_match, etag):
            return Cached(etag=etag, body=None)
        games = await games_repository.owned_summaries(self.session, user.id, kind=GameKind.SPADES)
        body = SpBootstrapOut(
            my_games=[sp_summary_out(g) for g in games], data_version=data_version
        )
        return Cached(etag=etag, body=body)
