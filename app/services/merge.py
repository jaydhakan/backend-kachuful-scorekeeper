"""Merging two registry players into one (admin only)."""

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, Player
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.players import players_repository
from app.repositories.users import users_repository
from app.schemas.admin import PlayerEnvelopeOut
from app.services.presenters import player_out

logger = get_logger(__name__)


class MergeService:
    """`POST /api/admin/players/{id}/merge`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _lock_pair(self, from_id: uuid.UUID, into_id: uuid.UUID) -> tuple[Player, Player]:
        # Lock in a fixed order so two concurrent merges can't deadlock.
        locked: dict[uuid.UUID, Player | None] = {}
        for player_id in sorted((from_id, into_id)):
            locked[player_id] = await players_repository.get_for_update(self.session, player_id)
        source, target = locked[from_id], locked[into_id]
        if source is None or target is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return source, target

    async def merge(self, from_id: uuid.UUID, into_id: uuid.UUID) -> PlayerEnvelopeOut:
        """Fold `from_id` into `into_id`: every reference moves and `from_id` gets `mergedInto`."""
        if from_id == into_id:
            raise ApiError(
                422, ErrorCode.INVALID, "Pick two different players.", fields={"intoId": "same"}
            )
        source, target = await self._lock_pair(from_id, into_id)
        if source.merged_into is not None or target.merged_into is not None:
            raise ApiError(
                422,
                ErrorCode.INVALID,
                "One of these players was already merged.",
                fields={"intoId": "already merged"},
            )
        source_user = await users_repository.get_by_player(self.session, source.id)
        target_user = await users_repository.get_by_player(self.session, target.id)
        if source_user is not None and target_user is not None:
            raise ApiError(409, ErrorCode.BOTH_LINKED, "Both players have accounts.")
        await self._move_references(source.id, target.id)
        if source_user is not None:
            await users_repository.move_player_link(self.session, source.id, target.id)
        await players_repository.redirect_merged(self.session, source.id, target.id)
        await players_repository.touch(self.session, target)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("players merged", extra={"from": str(source.id), "into": str(target.id)})
        view = await players_repository.get_view(self.session, target.id)
        assert view is not None
        return PlayerEnvelopeOut(player=player_out(view))

    async def _move_references(self, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Invite).where(Invite.player_id == from_id).values(player_id=into_id)
        )
