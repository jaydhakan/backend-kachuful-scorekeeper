"""Player registry use cases: create, check, edit, list."""

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.domain.names import is_valid_display_name, name_key, tidy_spaces
from app.domain.time import from_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.app_state import app_state_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.common import Page
from app.schemas.players import (
    NameCheckOut,
    NewPlayerIn,
    PlayerOut,
    PlayerPatchIn,
    PlayerSummaryOut,
)
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_out, player_summary_out


@dataclass(frozen=True)
class CreatedPlayer:
    """The stored player, and whether this call created it (vs an idempotent repeat)."""

    view: PlayerView
    created: bool


def _optional(text: str | None) -> str | None:
    tidied = tidy_spaces(text) if text is not None else ""
    return tidied or None


def _invalid_name() -> ApiError:
    return ApiError(
        422,
        ErrorCode.INVALID,
        "Display names are 1-14 characters.",
        fields={"displayName": "must be 1-14 characters"},
    )


def _same_body(player: Player, body: NewPlayerIn) -> bool:
    return (
        player.display_name == tidy_spaces(body.display_name)
        and player.first_name == _optional(body.first_name)
        and player.last_name == _optional(body.last_name)
        and player.emoji == body.emoji
        and player.color == body.color
    )


class PlayersService:
    """Registry operations for signed-in users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary(self, player_id: uuid.UUID) -> PlayerSummaryOut:
        """One player as a PlayerSummary; 404 when unknown."""
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return player_summary_out(view)

    async def name_taken_error(self, holder: Player) -> ApiError:
        """409 `name_taken` carrying who holds the name."""
        view = await players_repository.get_view(self.session, holder.id)
        assert view is not None
        return ApiError(
            409,
            ErrorCode.NAME_TAKEN,
            f"{holder.display_name} is already a player.",
            existing=player_summary_out(view).model_dump(mode="json"),
        )

    async def create(self, body: NewPlayerIn, *, created_by: uuid.UUID) -> CreatedPlayer:
        """Create a player; same id and body again is a no-op. Does not commit."""
        if not is_valid_display_name(body.display_name):
            raise _invalid_name()
        existing = await players_repository.get(self.session, body.id)
        if existing is not None:
            if not _same_body(existing, body):
                raise ApiError(
                    409, ErrorCode.ID_CONFLICT, "A different player already has this id."
                )
            view = await players_repository.get_view(self.session, existing.id)
            assert view is not None
            return CreatedPlayer(view=view, created=False)
        key = name_key(body.display_name)
        holder = await players_repository.get_active_by_name_key(self.session, key)
        if holder is not None:
            raise await self.name_taken_error(holder)
        player = Player(
            id=body.id,
            display_name=tidy_spaces(body.display_name),
            name_key=key,
            first_name=_optional(body.first_name),
            last_name=_optional(body.last_name),
            emoji=body.emoji,
            color=body.color,
            created_by=created_by,
        )
        try:
            async with self.session.begin_nested():
                await players_repository.create(self.session, player)
        except IntegrityError:
            # Lost a race for the same name or id: report it like the check above would have.
            holder = await players_repository.get_active_by_name_key(self.session, key)
            if holder is not None:
                raise await self.name_taken_error(holder) from None
            raise ApiError(409, ErrorCode.ID_CONFLICT, "This id is taken.") from None
        await app_state_repository.bump(self.session)
        view = await players_repository.get_view(self.session, player.id)
        assert view is not None
        return CreatedPlayer(view=view, created=True)

    async def create_and_commit(self, body: NewPlayerIn, user: User) -> CreatedPlayer:
        """`POST /api/players`."""
        result = await self.create(body, created_by=user.id)
        await self.session.commit()
        return result

    async def check(self, name: str) -> NameCheckOut:
        """Whether a display name is free."""
        holder = await players_repository.get_active_by_name_key(self.session, name_key(name))
        if holder is None:
            return NameCheckOut(available=True)
        view = await players_repository.get_view(self.session, holder.id)
        assert view is not None
        return NameCheckOut(available=False, existing=player_summary_out(view))

    async def patch(self, player_id: uuid.UUID, body: PlayerPatchIn, user: User) -> PlayerOut:
        """Edit a player under the §2 permission rules."""
        player = await players_repository.get_for_update(self.session, player_id)
        if player is None or player.merged_into is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        is_admin = user.role == "admin"
        is_linked = user.player_id == player.id
        is_creator = player.created_by == user.id
        sent = body.model_fields_set
        if "display_name" in sent and not (is_admin or is_linked):
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the admin or this player can rename.")
        if sent - {"display_name"} and not (is_admin or is_linked or is_creator):
            raise ApiError(403, ErrorCode.FORBIDDEN, "You can't edit this player.")
        if "display_name" in sent and body.display_name is not None:
            await self._rename(player, body.display_name)
        for field in ("first_name", "last_name"):
            if field in sent:
                setattr(player, field, _optional(getattr(body, field)))
        for field in ("emoji", "color"):
            value = getattr(body, field)
            if field in sent and value is not None:
                setattr(player, field, value)
        await players_repository.touch(self.session, player)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        view = await players_repository.get_view(self.session, player.id)
        assert view is not None
        return player_out(view)

    async def _rename(self, player: Player, display_name: str) -> None:
        if not is_valid_display_name(display_name):
            raise _invalid_name()
        key = name_key(display_name)
        holder = await players_repository.get_active_by_name_key(self.session, key)
        if holder is not None and holder.id != player.id:
            raise await self.name_taken_error(holder)
        player.display_name = tidy_spaces(display_name)
        player.name_key = key

    async def list_page(
        self, *, updated_since: int | None, cursor: str | None, limit: int
    ) -> Page[PlayerOut]:
        """The registry in sync order, merged players included."""
        after = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            after = (at, _parse_uuid(key))
        views = await players_repository.list_views(
            self.session,
            updated_since=None if updated_since is None else from_ms(updated_since),
            after=after,
            limit=limit + 1,
        )
        items = [player_out(v) for v in views[:limit]]
        next_cursor = None
        if len(views) > limit:
            last = views[limit - 1].player
            next_cursor = encode_cursor(last.updated_at, str(last.id))
        return Page[PlayerOut](items=items, next_cursor=next_cursor)


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
