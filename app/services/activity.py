"""The activity feed."""

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Activity, User
from app.domain.enums import GameKind, ProgressionGame, kinds_of
from app.domain.time import to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.achievements import achievements_repository
from app.repositories.games import SummaryRow, games_repository
from app.repositories.ishaara import IshaaraListRow, ishaara_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.boards import ActivityItemOut
from app.schemas.common import Page
from app.schemas.games import GameSummaryOut
from app.schemas.ishaara import IshaaraSummaryOut
from app.schemas.kaali_teeri import KtGameSummaryOut
from app.schemas.players import PlayerSummaryOut
from app.schemas.spades import SpGameSummaryOut
from app.schemas.spades_tables import SpTableFeedOut
from app.services.ishaara_games import ishaara_summary_out
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import kt_summary_out, player_summary_out, sp_summary_out, summary_out
from app.services.spades_state import table_summary_out

type AnySummary = (
    GameSummaryOut | KtGameSummaryOut | SpGameSummaryOut | SpTableFeedOut | IshaaraSummaryOut
)


class ActivityService:
    """`GET /api/activity`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self,
        game: ProgressionGame | None,
        cursor: str | None,
        limit: int,
        user: User,
    ) -> Page[ActivityItemOut]:
        """One game's feed (None: every game's), newest first, players and games filled in.

        Three queries for one game's page (Spades: four, both kinds); up to seven for every game.
        """
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            if not key.isdigit():
                raise ApiError(
                    422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
                )
            before = (at, int(key))
        rows = await achievements_repository.activity_page(
            self.session, game=game, before=before, limit=limit + 1
        )
        page = rows[:limit]
        views = await players_repository.get_views(
            self.session, [r.player_id for r in page if r.player_id is not None]
        )
        games = await self._summaries(page, user)
        items = [
            ActivityItemOut(
                id=r.id,
                game_kind=ProgressionGame(r.game),
                kind=r.kind,
                player=_player(views, r.player_id),
                game=None if r.game_id is None else games.get(r.game_id),
                data=r.data,
                created_at=to_ms(r.created_at),
            )
            for r in page
        ]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.created_at, str(last.id))
        return Page[ActivityItemOut](items=items, next_cursor=next_cursor)

    async def _summaries(self, page: Sequence[Activity], user: User) -> dict[uuid.UUID, AnySummary]:
        """Each game's summary in its own kind's shape: one query per kind on the page."""
        ids_by_game: dict[ProgressionGame, list[uuid.UUID]] = defaultdict(list)
        for r in page:
            if r.game_id is not None:
                ids_by_game[ProgressionGame(r.game)].append(r.game_id)
        out: dict[uuid.UUID, AnySummary] = {}
        for game, ids in ids_by_game.items():
            for kind in kinds_of(game):
                rows = await games_repository.summaries(self.session, ids, kind=kind)
                out |= await self._present(kind, list(rows.values()), user)
        return out

    async def _present(
        self, kind: GameKind, rows: list[SummaryRow], user: User
    ) -> dict[uuid.UUID, AnySummary]:
        if kind == GameKind.ISHAARA:
            return await self._ishaara(rows, user)
        return {r.game_id: _summary(kind, r, user) for r in rows}

    async def _ishaara(self, rows: list[SummaryRow], user: User) -> dict[uuid.UUID, AnySummary]:
        """Ishaara's items say which team this account gives clues for (one query)."""
        if not rows:
            return {}
        teams = await ishaara_repository.clue_teams(
            self.session, user.id, [r.game_id for r in rows]
        )
        return {
            r.game_id: ishaara_summary_out(
                IshaaraListRow(r.game_id, r.summary, r.owner_name, r.updated_at, r.owner_id),
                teams.get(r.game_id),
                user.id,
            )
            for r in rows
        }


def _summary(kind: GameKind, row: SummaryRow, user: User) -> AnySummary:
    if kind == GameKind.KAALI_TEERI:
        return kt_summary_out(row)
    if kind == GameKind.SPADES:
        return sp_summary_out(row)
    if kind == GameKind.SPADES_ONLINE:
        # Never the join code: any signed-in reader sees the feed, open tables included.
        table = table_summary_out(
            row.game_id, row.summary, player_id=user.player_id, is_host=row.owner_id == user.id
        )
        return SpTableFeedOut.model_validate(table.model_dump(exclude={"code"}))
    return summary_out(row)


def _player(
    views: dict[uuid.UUID, PlayerView], player_id: uuid.UUID | None
) -> PlayerSummaryOut | None:
    view = None if player_id is None else views.get(player_id)
    return None if view is None else player_summary_out(view)
