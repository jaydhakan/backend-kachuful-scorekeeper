"""The player registry."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    delete,
    exists,
    func,
    literal_column,
    or_,
    select,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import JSON, aggregate_order_by, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Achievement,
    Activity,
    Game,
    Invite,
    Player,
    PlayerAlias,
    PlayerPhoto,
    User,
)
from app.repositories.seats import seat_rows, seated_anywhere


@dataclass(frozen=True)
class AliasView:
    """Another name of a player, as reads show it."""

    display_name: str
    first_name: str | None
    last_name: str | None


@dataclass(frozen=True)
class PlayerView:
    """A player with what the contract's Player and PlayerSummary add from other tables."""

    player: Player
    linked_user_id: uuid.UUID | None
    created_by_name: str
    has_photo: bool = False
    aliases: tuple[AliasView, ...] = ()


@dataclass(frozen=True)
class DuplicatePair:
    """Two live players who may be one person, and why."""

    a_id: uuid.UUID
    b_id: uuid.UUID
    reason: str


_linked = aliased(User)
_creator = aliased(User)
_creator_player = aliased(Player)


def _aliases_json() -> Any:
    # One correlated aggregate per row keeps every player read a single query.
    entry = func.json_build_object(
        "display_name",
        PlayerAlias.display_name,
        "first_name",
        PlayerAlias.first_name,
        "last_name",
        PlayerAlias.last_name,
    )
    ordered = aggregate_order_by(entry, PlayerAlias.created_at, PlayerAlias.id)
    return (
        select(func.coalesce(func.json_agg(ordered), literal_column("'[]'::json"), type_=JSON))
        .where(PlayerAlias.player_id == Player.id)
        .correlate(Player)
        .scalar_subquery()
    )


def _view_query() -> Select[Player, uuid.UUID | None, str, bool, Any]:
    # Joins only the photo's key: the image bytes are never loaded here.
    return (
        select(
            Player,
            _linked.id,
            func.coalesce(_creator_player.display_name, ""),
            PlayerPhoto.player_id.is_not(None),
            _aliases_json(),
        )
        .outerjoin(_linked, _linked.player_id == Player.id)
        .outerjoin(_creator, _creator.id == Player.created_by)
        .outerjoin(_creator_player, _creator_player.id == _creator.player_id)
        .outerjoin(PlayerPhoto, PlayerPhoto.player_id == Player.id)
    )


def _to_views(rows: Sequence[Any]) -> list[PlayerView]:
    return [
        PlayerView(
            player=r[0],
            linked_user_id=r[1],
            created_by_name=r[2],
            has_photo=bool(r[3]),
            aliases=tuple(AliasView(**a) for a in r[4] or ()),
        )
        for r in rows
    ]


class PlayersRepository:
    """Data access for `players`."""

    async def get(self, session: AsyncSession, player_id: uuid.UUID) -> Player | None:
        """A player by id."""
        return await session.get(Player, player_id)

    async def get_for_update(self, session: AsyncSession, player_id: uuid.UUID) -> Player | None:
        """A player by id, row-locked for the rest of the transaction."""
        stmt = select(Player).where(Player.id == player_id).with_for_update()
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_many(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Player]:
        """Players by id, in one query."""
        if not player_ids:
            return {}
        stmt = select(Player).where(Player.id.in_(set(player_ids)))
        return {p.id: p for p in (await session.execute(stmt)).scalars()}

    async def get_view(self, session: AsyncSession, player_id: uuid.UUID) -> PlayerView | None:
        """One player with link and creator name."""
        views = await self.get_views(session, [player_id])
        return views.get(player_id)

    async def get_views(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PlayerView]:
        """Players with link and creator name, in one query."""
        if not player_ids:
            return {}
        stmt = _view_query().where(Player.id.in_(set(player_ids)))
        return {v.player.id: v for v in _to_views((await session.execute(stmt)).all())}

    async def list_views(
        self,
        session: AsyncSession,
        *,
        updated_since: datetime | None,
        after: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[PlayerView]:
        """Registry page in sync order (updated_at, id), merged players included."""
        stmt = _view_query().order_by(Player.updated_at, Player.id).limit(limit)
        if updated_since is not None:
            stmt = stmt.where(Player.updated_at > updated_since)
        if after is not None:
            at, last_id = after
            stmt = stmt.where(
                or_(Player.updated_at > at, and_(Player.updated_at == at, Player.id > last_id))
            )
        return _to_views((await session.execute(stmt)).all())

    async def list_all_views(self, session: AsyncSession) -> list[PlayerView]:
        """The whole registry (bootstrap)."""
        stmt = _view_query().order_by(Player.updated_at, Player.id)
        return _to_views((await session.execute(stmt)).all())

    async def get_active_by_name_or_alias(self, session: AsyncSession, key: str) -> Player | None:
        """The unmerged player holding a name key as display name or as an alias."""
        # A union keeps both lookups on their unique indexes (an OR would scan the players).
        holders = union(
            select(Player.id).where(Player.name_key == key, Player.merged_into.is_(None)),
            select(PlayerAlias.player_id).where(PlayerAlias.name_key == key),
        )
        stmt = select(Player).where(Player.merged_into.is_(None), Player.id.in_(holders))
        return (await session.execute(stmt)).scalars().first()

    async def aliases_of(self, session: AsyncSession, player_id: uuid.UUID) -> list[PlayerAlias]:
        """A player's aliases, oldest first."""
        stmt = (
            select(PlayerAlias)
            .where(PlayerAlias.player_id == player_id)
            .order_by(PlayerAlias.created_at, PlayerAlias.id)
        )
        return list((await session.execute(stmt)).scalars())

    async def add_aliases(self, session: AsyncSession, aliases: Sequence[PlayerAlias]) -> None:
        """Insert aliases (the caller has checked the names)."""
        session.add_all(aliases)
        await session.flush()

    async def move_aliases(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Give `into_id` every alias of `from_id` (merge)."""
        await session.execute(
            update(PlayerAlias).where(PlayerAlias.player_id == from_id).values(player_id=into_id)
        )

    async def get_active_by_email(self, session: AsyncSession, email: str) -> Player | None:
        """The unmerged player holding an email (case-insensitive)."""
        stmt = select(Player).where(Player.email == email, Player.merged_into.is_(None))
        return (await session.execute(stmt)).scalar_one_or_none()

    async def unlinked_by_email(self, session: AsyncSession, email: str) -> PlayerView | None:
        """The unmerged player with this email, if no account is linked to it."""
        stmt = _view_query().where(
            Player.email == email, Player.merged_into.is_(None), _linked.id.is_(None)
        )
        views = _to_views((await session.execute(stmt)).all())
        return views[0] if views else None

    async def photo(self, session: AsyncSession, player_id: uuid.UUID) -> bytes | None:
        """A player's stored photo (WebP bytes)."""
        stmt = select(PlayerPhoto.image).where(PlayerPhoto.player_id == player_id)
        image: bytes | None = (await session.execute(stmt)).scalar_one_or_none()
        return image

    async def save_photo(self, session: AsyncSession, player_id: uuid.UUID, image: bytes) -> None:
        """Insert or replace a player's photo."""
        stmt = (
            insert(PlayerPhoto)
            .values(player_id=player_id, image=image)
            .on_conflict_do_update(
                index_elements=[PlayerPhoto.player_id],
                set_={"image": image, "updated_at": func.now()},
            )
        )
        await session.execute(stmt)

    async def delete_photo(self, session: AsyncSession, player_id: uuid.UUID) -> bool:
        """Remove a player's photo; False when there was none."""
        stmt = (
            delete(PlayerPhoto)
            .where(PlayerPhoto.player_id == player_id)
            .returning(PlayerPhoto.player_id)
        )
        return (await session.execute(stmt)).first() is not None

    async def move_photo(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Give `into_id` the photo of `from_id` (merge)."""
        await session.execute(
            update(PlayerPhoto).where(PlayerPhoto.player_id == from_id).values(player_id=into_id)
        )

    async def photo_revs(
        self, session: AsyncSession, player_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """photo_rev of the players that have a photo (one query)."""
        if not player_ids:
            return {}
        stmt = (
            select(Player.id, Player.photo_rev)
            .join(PlayerPhoto, PlayerPhoto.player_id == Player.id)
            .where(Player.id.in_(set(player_ids)))
        )
        return {row.id: row.photo_rev for row in (await session.execute(stmt)).all()}

    async def create(self, session: AsyncSession, player: Player) -> Player:
        """Insert a player (the caller has checked the name)."""
        session.add(player)
        await session.flush()
        await session.refresh(player)
        return player

    async def delete_unused_created_by(self, session: AsyncSession, user_id: uuid.UUID) -> int:
        """Delete players this user added that no game (of any kind), account, invite or merge uses."""
        merged_here = aliased(Player)
        unused = select(Player.id).where(
            Player.created_by == user_id,
            ~exists().where(User.player_id == Player.id),
            ~seated_anywhere(Player.id),
            ~exists().where(Invite.player_id == Player.id),
            ~exists().where(merged_here.merged_into == Player.id),
        )
        ids = list((await session.execute(unused)).scalars())
        if not ids:
            return 0
        await session.execute(delete(Activity).where(Activity.player_id.in_(ids)))
        await session.execute(delete(Achievement).where(Achievement.player_id.in_(ids)))
        await session.execute(delete(Player).where(Player.id.in_(ids)))
        return len(ids)

    async def touch(self, session: AsyncSession, player: Player) -> None:
        """Bump updated_at so sync clients pick the change up."""
        player.updated_at = func.now()
        await session.flush()
        await session.refresh(player)

    async def redirect_merged(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """Mark a player merged, and repoint players already merged into it."""
        now = func.now()
        await session.execute(
            update(Player)
            .where(Player.merged_into == from_id)
            .values(merged_into=into_id, updated_at=now)
        )
        await session.execute(
            update(Player).where(Player.id == from_id).values(merged_into=into_id, updated_at=now)
        )

    async def possible_duplicates(
        self, session: AsyncSession, *, min_similarity: float, limit: int
    ) -> list[DuplicatePair]:
        """Live pairs that could still be merged: an account's email on another player, then
        trigram-similar name keys (most similar first)."""
        by_email = await self._email_pairs(session, limit)
        return by_email + await self._name_pairs(session, min_similarity, limit - len(by_email))

    async def _email_pairs(self, session: AsyncSession, limit: int) -> list[DuplicatePair]:
        a, b, user = aliased(Player), aliased(Player), aliased(User)
        stmt = (
            select(a.id, b.id)
            .join(user, user.player_id == a.id)
            .join(b, and_(b.email == user.email, b.id != a.id))
            .where(a.merged_into.is_(None), b.merged_into.is_(None), _unlinked(b), _apart(a, b))
            .order_by(a.id, b.id)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [DuplicatePair(a_id=r[0], b_id=r[1], reason="email") for r in rows]

    async def _name_pairs(
        self, session: AsyncSession, min_similarity: float, limit: int
    ) -> list[DuplicatePair]:
        if limit <= 0:
            return []
        a, b = aliased(Player), aliased(Player)
        score = func.similarity(a.name_key, b.name_key)
        # `%` lets the trigram index find candidates; the score then applies our own threshold.
        stmt = (
            select(a.id, b.id)
            .join(b, and_(a.id < b.id, a.name_key.op("%")(b.name_key)))
            .where(
                a.merged_into.is_(None),
                b.merged_into.is_(None),
                score >= min_similarity,
                or_(_unlinked(a), _unlinked(b)),
                _apart(a, b),
            )
            .order_by(score.desc(), a.id, b.id)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [DuplicatePair(a_id=r[0], b_id=r[1], reason="name") for r in rows]


def _unlinked(player: Any) -> ColumnElement[bool]:
    return ~exists().where(User.player_id == player.id)


def _apart(a: Any, b: Any) -> ColumnElement[bool]:
    """Never seated in the same live game of any kind (a merge would refuse them)."""
    mine, theirs = seat_rows("mine"), seat_rows("theirs")
    together = (
        select(1)
        .select_from(mine)
        .join(theirs, theirs.c.game_id == mine.c.game_id)
        .join(Game, Game.id == mine.c.game_id)
        .where(mine.c.player_id == a.id, theirs.c.player_id == b.id, Game.deleted_at.is_(None))
    )
    return ~together.exists()


players_repository = PlayersRepository()
