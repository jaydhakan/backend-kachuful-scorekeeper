"""Reading achievements: lists, per player, per game."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.time import to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.achievements import achievements_repository
from app.schemas.common import Page
from app.schemas.games import AchievementOut
from app.services.pagination import decode_cursor, encode_cursor


def achievement_out(row: Achievement) -> AchievementOut:
    """The contract's Achievement."""
    return AchievementOut(
        id=row.id,
        code=row.code,
        player_id=row.player_id,
        game_id=row.game_id,
        round_index=row.round_index,
        period=row.period,
        earned_at=to_ms(row.earned_at),
        data=row.data,
    )


async def achievements_for_player(
    session: AsyncSession, player_id: uuid.UUID
) -> list[AchievementOut]:
    """Every achievement of one player, newest first."""
    return [achievement_out(r) for r in await achievements_repository.of_player(session, player_id)]


async def achievements_for_game(session: AsyncSession, game_id: uuid.UUID) -> list[AchievementOut]:
    """Every achievement tied to one game, oldest first."""
    return [achievement_out(r) for r in await achievements_repository.of_game(session, game_id)]


async def achievements_page(
    session: AsyncSession, *, player_id: uuid.UUID | None, cursor: str | None, limit: int
) -> Page[AchievementOut]:
    """`GET /api/achievements`: newest first."""
    before = None
    if cursor is not None:
        at, key = decode_cursor(cursor)
        try:
            before = (at, uuid.UUID(key))
        except ValueError as exc:
            raise ApiError(
                422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
            ) from exc
    rows = await achievements_repository.list_page(
        session, player_id=player_id, before=before, limit=limit + 1
    )
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last.earned_at, str(last.id))
    return Page[AchievementOut](
        items=[achievement_out(r) for r in rows[:limit]], next_cursor=next_cursor
    )
