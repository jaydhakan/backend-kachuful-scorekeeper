"""Player registry use cases. A person is the same player only when name AND email both match."""

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, PlayerAlias, User
from app.domain.names import clean_email, is_valid_display_name, name_key, tidy_spaces
from app.domain.time import from_ms
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.render import run_in_thread
from app.render.photo import ALLOWED_TYPES, MAX_UPLOAD_BYTES, UnsupportedImageError, reencode
from app.repositories.app_state import app_state_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.common import Page
from app.schemas.players import (
    NewAliasIn,
    NewPlayerIn,
    PhotoOut,
    PlayerCheckOut,
    PlayerOut,
    PlayerPatchIn,
    PlayerSummaryOut,
)
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import photo_url, player_out, player_summary_out

logger = get_logger(__name__)


@dataclass(frozen=True)
class CreatedPlayer:
    """The stored player, and whether this call created it (vs an idempotent repeat)."""

    view: PlayerView
    created: bool


@dataclass(frozen=True)
class Holders:
    """Who holds a name and who holds an email, among live players."""

    by_name: Player | None
    by_email: Player | None

    @property
    def same(self) -> Player | None:
        """The one player holding both, if any."""
        if self.by_name is not None and self.by_email is not None:
            return self.by_name if self.by_name.id == self.by_email.id else None
        return None


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


def _invalid_email() -> ApiError:
    return ApiError(
        422, ErrorCode.INVALID, "Give a valid email.", fields={"email": "must look like x@y.z"}
    )


def required_email(raw: str | None) -> str:
    """A cleaned email; 422 `invalid` when missing or malformed."""
    email = None if raw is None else clean_email(raw)
    if email is None:
        raise _invalid_email()
    return email


def name_taken() -> ApiError:
    """409 `name_taken`; says nothing about who holds the name."""
    return ApiError(409, ErrorCode.NAME_TAKEN, "That name belongs to another player.")


def email_taken() -> ApiError:
    """409 `email_taken`; says nothing about who holds the email."""
    return ApiError(409, ErrorCode.EMAIL_TAKEN, "That email belongs to another player.")


def can_edit_details(user: User, player: Player) -> bool:
    """Admin, the player's creator or the player's linked user."""
    return user.role == "admin" or player.created_by == user.id or user.player_id == player.id


def _same_body(player: Player, body: NewPlayerIn, email: str) -> bool:
    return (
        player.display_name == tidy_spaces(body.display_name)
        and (player.email or "") == email
        and player.first_name == _optional(body.first_name)
        and player.last_name == _optional(body.last_name)
        and player.emoji == body.emoji
        and player.color == body.color
    )


class PlayersService:
    """Registry operations for signed-in users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _view(self, player_id: uuid.UUID) -> PlayerView:
        view = await players_repository.get_view(self.session, player_id)
        assert view is not None
        return view

    async def summary(self, player_id: uuid.UUID) -> PlayerSummaryOut:
        """One player as a PlayerSummary; 404 when unknown."""
        view = await players_repository.get_view(self.session, player_id)
        if view is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return player_summary_out(view)

    async def holders(self, display_name: str, email: str) -> Holders:
        """The live players holding this name (as name or alias) and this email."""
        return Holders(
            by_name=await players_repository.get_active_by_name_or_alias(
                self.session, name_key(display_name)
            ),
            by_email=await players_repository.get_active_by_email(self.session, email),
        )

    async def _conflict(self, holders: Holders, *, reveal_match: bool) -> ApiError | None:
        same = holders.same
        if same is not None and reveal_match:
            summary = player_summary_out(await self._view(same.id))
            return ApiError(
                409,
                ErrorCode.SAME_PLAYER,
                f"{same.display_name} is already a player.",
                player=summary.model_dump(mode="json"),
            )
        if holders.by_email is not None:
            return email_taken()
        if holders.by_name is not None:
            return name_taken()
        return None

    async def create(
        self,
        body: NewPlayerIn,
        *,
        created_by: uuid.UUID,
        email: str,
        reveal_match: bool = True,
    ) -> CreatedPlayer:
        """Create a player; same id and body again is a no-op. Does not commit."""
        if not is_valid_display_name(body.display_name):
            raise _invalid_name()
        existing = await players_repository.get(self.session, body.id)
        if existing is not None:
            if not _same_body(existing, body, email):
                raise ApiError(
                    409, ErrorCode.ID_CONFLICT, "A different player already has this id."
                )
            return CreatedPlayer(view=await self._view(existing.id), created=False)
        conflict = await self._conflict(
            await self.holders(body.display_name, email), reveal_match=reveal_match
        )
        if conflict is not None:
            raise conflict
        player = Player(
            id=body.id,
            display_name=tidy_spaces(body.display_name),
            name_key=name_key(body.display_name),
            email=email,
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
            # Lost a race for the same name, email or id: answer as the checks above would have.
            conflict = await self._conflict(
                await self.holders(body.display_name, email), reveal_match=reveal_match
            )
            raise conflict or ApiError(409, ErrorCode.ID_CONFLICT, "This id is taken.") from None
        await app_state_repository.bump(self.session)
        return CreatedPlayer(view=await self._view(player.id), created=True)

    async def create_and_commit(self, body: NewPlayerIn, user: User) -> tuple[PlayerOut, bool]:
        """`POST /api/players` (email required): the player, and whether it is new."""
        result = await self.create(body, created_by=user.id, email=required_email(body.email))
        await self.session.commit()
        return player_out(result.view, user), result.created

    async def check(self, display_name: str, raw_email: str) -> PlayerCheckOut:
        """match (name or alias and email are one player) / email_match / name_taken / new.

        email_match names the email's player so the app can ask "Is this them?" (invite-only group).
        """
        holders = await self.holders(display_name, required_email(raw_email))
        same = holders.same
        if same is not None:
            return PlayerCheckOut(
                status="match", player=player_summary_out(await self._view(same.id))
            )
        if holders.by_email is not None:
            return PlayerCheckOut(
                status="email_match",
                player=player_summary_out(await self._view(holders.by_email.id)),
            )
        if holders.by_name is not None:
            return PlayerCheckOut(status="name_taken")
        return PlayerCheckOut(status="new")

    async def _editable(self, player_id: uuid.UUID, user: User) -> Player:
        player = await players_repository.get_for_update(self.session, player_id)
        if player is None or player.merged_into is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        if not can_edit_details(user, player):
            raise ApiError(403, ErrorCode.FORBIDDEN, "You can't edit this player.")
        return player

    async def patch(self, player_id: uuid.UUID, body: PlayerPatchIn, user: User) -> PlayerOut:
        """Edit a player: admin, the creator or the linked user (see `can_edit_details`)."""
        player = await self._editable(player_id, user)
        sent = body.model_fields_set
        if "display_name" in sent:
            await self._rename(player, body.display_name or "")
        if "email" in sent:
            await self._set_email(player, required_email(body.email))
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
        return player_out(await self._view(player.id), user)

    async def _rename(self, player: Player, display_name: str) -> None:
        if not is_valid_display_name(display_name):
            raise _invalid_name()
        key = name_key(display_name)
        holder = await players_repository.get_active_by_name_or_alias(self.session, key)
        if holder is not None and holder.id != player.id:
            raise name_taken()
        player.display_name = tidy_spaces(display_name)
        player.name_key = key

    async def add_alias(
        self, player_id: uuid.UUID, body: NewAliasIn, user: User
    ) -> tuple[PlayerOut, bool]:
        """Give a live player another name; any signed-in user may. The same name again is a no-op."""
        player = await players_repository.get_for_update(self.session, player_id)
        if player is None or player.merged_into is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        if not is_valid_display_name(body.display_name):
            raise _invalid_name()
        key = name_key(body.display_name)
        holder = await players_repository.get_active_by_name_or_alias(self.session, key)
        if holder is not None and holder.id != player.id:
            raise name_taken()
        created = holder is None
        if created:
            await self._insert_alias(player, body, key, user)
        return player_out(await self._view(player.id), user), created

    async def _insert_alias(self, player: Player, body: NewAliasIn, key: str, user: User) -> None:
        alias = PlayerAlias(
            player_id=player.id,
            display_name=tidy_spaces(body.display_name),
            name_key=key,
            first_name=_optional(body.first_name),
            last_name=_optional(body.last_name),
            created_by=user.id,
        )
        try:
            async with self.session.begin_nested():
                await players_repository.add_aliases(self.session, [alias])
        except IntegrityError:
            # Lost a race for the name: the unique index or the reserve trigger refused it.
            raise name_taken() from None
        await players_repository.touch(self.session, player)
        await app_state_repository.bump(self.session)
        await self.session.commit()

    async def _set_email(self, player: Player, email: str) -> None:
        holder = await players_repository.get_active_by_email(self.session, email)
        if holder is not None and holder.id != player.id:
            raise email_taken()
        player.email = email

    async def list_page(
        self, *, updated_since: int | None, cursor: str | None, limit: int, viewer: User
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
        items = [player_out(v, viewer) for v in views[:limit]]
        next_cursor = None
        if len(views) > limit:
            last = views[limit - 1].player
            next_cursor = encode_cursor(last.updated_at, str(last.id))
        return Page[PlayerOut](items=items, next_cursor=next_cursor)

    async def set_photo(
        self, player_id: uuid.UUID, data: bytes, content_type: str | None, user: User
    ) -> PhotoOut:
        """Store a re-encoded photo; the URL's version changes so caches pick it up."""
        player = await self._editable(player_id, user)
        if (content_type or "").split(";")[0].strip().lower() not in ALLOWED_TYPES:
            raise _unsupported()
        if len(data) > MAX_UPLOAD_BYTES:
            raise ApiError(413, ErrorCode.TOO_LARGE, "Photos can be at most 2 MB.")
        try:
            image = await run_in_thread(reencode, data)
        except UnsupportedImageError as exc:
            raise _unsupported() from exc
        await players_repository.save_photo(self.session, player.id, image)
        player.photo_rev += 1
        await players_repository.touch(self.session, player)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("player photo set", extra={"player_id": str(player.id), "bytes": len(image)})
        return PhotoOut(photo_url=photo_url(player.id, player.photo_rev))

    async def delete_photo(self, player_id: uuid.UUID, user: User) -> None:
        """Back to the emoji."""
        player = await self._editable(player_id, user)
        if await players_repository.delete_photo(self.session, player.id):
            player.photo_rev += 1
            await players_repository.touch(self.session, player)
            await app_state_repository.bump(self.session)
        await self.session.commit()

    async def photo(self, player_id: uuid.UUID) -> bytes:
        """The stored WebP; 404 when there is none."""
        image = await players_repository.photo(self.session, player_id)
        if image is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No photo.")
        return image


def _unsupported() -> ApiError:
    return ApiError(
        415, ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Send a WebP, JPEG or PNG image (at most 2 MB)."
    )


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
