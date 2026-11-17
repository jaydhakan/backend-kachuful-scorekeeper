"""`GET /api/achievements/ladders`: one player's ladder counts in one game."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProgressionGame
from app.exceptions import ApiError, ErrorCode
from app.repositories.players import players_repository
from app.schemas.boards import LaddersOut
from app.services.progression.registry import progression_of


async def ladders(
    session: AsyncSession, *, game: ProgressionGame, player_id: uuid.UUID
) -> LaddersOut:
    """Each of the game's ladders, counted the way its codes are earned; 404 for no player."""
    if await players_repository.get_view(session, player_id) is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
    counts = await progression_of(game).ladder_counts(session, player_id)
    return LaddersOut(game_kind=game, player_id=player_id, counts=counts)
