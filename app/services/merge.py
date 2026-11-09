"""Merging two registry players into one (admin only)."""

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Invite, Player, PlayerAlias, User
from app.domain.enums import GameKind
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import games_repository
from app.repositories.players import players_repository
from app.repositories.users import users_repository
from app.schemas.admin import MergeKeepIn, PlayerEnvelopeOut
from app.services.achievements import AchievementsEngine
from app.services.games import GamesService
from app.services.kt_games import KtGamesService
from app.services.presenters import player_out

logger = get_logger(__name__)

Reseat = Callable[[AsyncSession, Game, uuid.UUID, uuid.UUID], Awaitable[None]]


async def _reseat_kachuful(
    session: AsyncSession, row: Game, from_id: uuid.UUID, into_id: uuid.UUID
) -> None:
    await GamesService(session).reseat(row, from_id, into_id)


async def _reseat_kaali_teeri(
    session: AsyncSession, row: Game, from_id: uuid.UUID, into_id: uuid.UUID
) -> None:
    await KtGamesService(session).reseat(row, from_id, into_id)


# How each kind moves a seat in its payload and derived rows: one entry per kind that has a
# table in `repositories.seats.SEAT_TABLES`.
RESEAT_BY_KIND: dict[GameKind, Reseat] = {
    GameKind.KACHUFUL: _reseat_kachuful,
    GameKind.KAALI_TEERI: _reseat_kaali_teeri,
}


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

    async def merge(
        self,
        from_id: uuid.UUID,
        into_id: uuid.UUID,
        admin: User,
        *,
        keep: MergeKeepIn | None = None,
    ) -> PlayerEnvelopeOut:
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
        await achievements_repository.move_player(self.session, source.id, target.id)
        await AchievementsEngine(self.session).career(
            {target.id}, changed_from_ms=None, sequence_changed=True
        )
        if source_user is not None:
            await users_repository.move_player_link(self.session, source.id, target.id)
        await players_repository.redirect_merged(self.session, source.id, target.id)
        await self._keep_identity(source, target, keep or MergeKeepIn(), admin)
        await players_repository.touch(self.session, target)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("players merged", extra={"from": str(source.id), "into": str(target.id)})
        view = await players_repository.get_view(self.session, target.id)
        assert view is not None
        return PlayerEnvelopeOut(player=player_out(view, admin))

    async def _keep_identity(
        self, source: Player, target: Player, keep: MergeKeepIn, admin: User
    ) -> None:
        """Apply `keep`; the chosen side's email and photo win, the other's fills a gap."""
        # Runs after the duplicate is marked merged, so its name and email no longer count as taken.
        # Names first: a demoted display name takes the kept player's first and last name with it.
        await self._keep_names(source, target, from_main=keep.display_name == "from", admin=admin)
        from_first = keep.email == "from"
        if (from_first and source.email is not None) or target.email is None:
            target.email = target.email if source.email is None else source.email
        if keep.names == "from" and (source.first_name or source.last_name):
            target.first_name, target.last_name = source.first_name, source.last_name
        await self._keep_photo(source, target, from_first=keep.photo == "from")

    async def _keep_photo(self, source: Player, target: Player, *, from_first: bool) -> None:
        revs = await players_repository.photo_revs(self.session, [source.id, target.id])
        if source.id not in revs or (target.id in revs and not from_first):
            return
        await players_repository.delete_photo(self.session, target.id)
        await players_repository.move_photo(self.session, source.id, target.id)
        target.photo_rev += 1

    async def _keep_names(
        self, source: Player, target: Player, *, from_main: bool, admin: User
    ) -> None:
        """The duplicate's aliases move over; whichever display name is not kept becomes one."""
        await players_repository.move_aliases(self.session, source.id, target.id)
        demoted = target if from_main else source
        alias = PlayerAlias(
            player_id=target.id,
            display_name=demoted.display_name,
            name_key=demoted.name_key,
            first_name=demoted.first_name,
            last_name=demoted.last_name,
            created_by=admin.id,
        )
        if from_main:
            target.display_name, target.name_key = source.display_name, source.name_key
        await players_repository.add_aliases(self.session, [alias])

    async def _move_references(self, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Invite).where(Invite.player_id == from_id).values(player_id=into_id)
        )
        source_games = await games_repository.ids_with_player(self.session, from_id)
        shared = set(source_games) & set(
            await games_repository.ids_with_player(self.session, into_id)
        )
        if shared:
            raise ApiError(
                422,
                ErrorCode.INVALID,
                "These players sat at the same table, so they are different people.",
                fields={"intoId": "played in a game together"},
            )
        for game_id in source_games:
            await games_repository.lock(self.session, game_id)
            row = await games_repository.get(self.session, game_id, kind=None)
            assert row is not None
            await RESEAT_BY_KIND[GameKind(row.kind)](self.session, row, from_id, into_id)
        # Counters span every kind, so they are recounted once whatever each reseat did.
        await derived_repository.refresh_player_counters(self.session, {from_id, into_id})
