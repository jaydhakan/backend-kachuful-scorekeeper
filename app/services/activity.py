"""The activity feed."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.achievements import achievements_repository
from app.repositories.games import games_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.boards import ActivityItemOut
from app.schemas.common import Page
from app.schemas.players import PlayerSummaryOut
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_summary_out, summary_out


class ActivityService:
    """`GET /api/activity`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, cursor: str | None, limit: int) -> Page[ActivityItemOut]:
        """Newest first, with players and games filled in (three queries per page)."""
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            if not key.isdigit():
                raise ApiError(
                    422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
                )
            before = (at, int(key))
        rows = await achievements_repository.activity_page(
            self.session, before=before, limit=limit + 1
        )
        page = rows[:limit]
        views = await players_repository.get_views(
            self.session, [r.player_id for r in page if r.player_id is not None]
        )
        games = await games_repository.summaries(
            self.session, [r.game_id for r in page if r.game_id is not None]
        )
        items = [
            ActivityItemOut(
                id=r.id,
                kind=r.kind,
                player=_player(views, r.player_id),
                game=None
                if r.game_id is None or r.game_id not in games
                else summary_out(games[r.game_id]),
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


def _player(
    views: dict[uuid.UUID, PlayerView], player_id: uuid.UUID | None
) -> PlayerSummaryOut | None:
    view = None if player_id is None else views.get(player_id)
    return None if view is None else player_summary_out(view)
