"""Looks, saved looks, Sikke spends and gifts (rewards finish §3-§5).

`sikke_spends` is append-only: this module only inserts and reads it (a merge re-points its rows).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Gift, SikkeSpend, WardrobeLook, WardrobeSavedLook

SEASON_CHAMPION = "season_champion"

# A finished seat of any kind before a time: the same seats `sikke_repository` pays for.
_PLAYED_BEFORE = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM (
            SELECT game_id FROM game_players
            WHERE player_id = :player_id AND place IS NOT NULL AND ended_at < :before
            UNION ALL
            SELECT game_id FROM kt_game_players
            WHERE player_id = :player_id AND ended_at < :before
            UNION ALL
            SELECT game_id FROM ishaara_game_players
            WHERE player_id = :player_id AND finished_at < :before
            UNION ALL
            SELECT game_id FROM spades_game_players
            WHERE player_id = :player_id AND result IS NOT NULL AND finished_at < :before
        ) s
        JOIN games g ON g.id = s.game_id
        WHERE g.deleted_at IS NULL
    )
    """
)


@dataclass(frozen=True)
class StoredPair:
    """An owned pair from a purchase or a gift, and when."""

    item_id: str
    variant: int
    at: datetime


@dataclass(frozen=True)
class StoredPairs:
    """A player's bought and received pairs."""

    bought: list[StoredPair]
    gifts: list[StoredPair]


class WardrobeRepository:
    """Data access for the Wardrobe, the Bazaar's spends and gifts."""

    # --- Looks ------------------------------------------------------------------------------

    async def look(self, session: AsyncSession, player_id: uuid.UUID) -> dict[str, Any] | None:
        """A player's worn look, or None when never set."""
        stmt = select(WardrobeLook.look).where(WardrobeLook.player_id == player_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def looks(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Some players' worn looks (players with none are missing)."""
        stmt = select(WardrobeLook.player_id, WardrobeLook.look).where(
            WardrobeLook.player_id.in_(set(player_ids))
        )
        return {row[0]: row[1] for row in (await session.execute(stmt)).all()}

    async def save_look(
        self, session: AsyncSession, player_id: uuid.UUID, look: dict[str, Any]
    ) -> None:
        """Set a player's worn look."""
        stmt = insert(WardrobeLook).values(player_id=player_id, look=look)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WardrobeLook.player_id],
            set_={"look": stmt.excluded.look, "updated_at": func.now()},
        )
        await session.execute(stmt)

    async def saved_looks(
        self, session: AsyncSession, player_id: uuid.UUID
    ) -> list[WardrobeSavedLook]:
        """A player's saved looks, by slot."""
        stmt = (
            select(WardrobeSavedLook)
            .where(WardrobeSavedLook.player_id == player_id)
            .order_by(WardrobeSavedLook.slot)
        )
        return list((await session.execute(stmt)).scalars())

    async def save_saved_look(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        *,
        slot: int,
        name: str,
        look: dict[str, Any],
    ) -> None:
        """Set one of a player's saved looks."""
        stmt = insert(WardrobeSavedLook).values(
            player_id=player_id, slot=slot, name=name, look=look
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WardrobeSavedLook.player_id, WardrobeSavedLook.slot],
            set_={"name": stmt.excluded.name, "look": stmt.excluded.look, "updated_at": func.now()},
        )
        await session.execute(stmt)

    async def delete_saved_look(
        self, session: AsyncSession, player_id: uuid.UUID, slot: int
    ) -> None:
        """Clear one of a player's saved looks (a no-op when empty)."""
        await session.execute(
            delete(WardrobeSavedLook).where(
                WardrobeSavedLook.player_id == player_id, WardrobeSavedLook.slot == slot
            )
        )

    # --- Ownership facts --------------------------------------------------------------------

    async def stored_pairs(self, session: AsyncSession, player_id: uuid.UUID) -> StoredPairs:
        """A player's purchases and gifts received, oldest first (one query)."""
        bought = select(
            text("'bought'"), SikkeSpend.item_id, SikkeSpend.variant, SikkeSpend.created_at
        ).where(SikkeSpend.player_id == player_id, SikkeSpend.kind == "purchase")
        gifts = select(text("'gift'"), Gift.item_id, Gift.variant, Gift.sent_at).where(
            Gift.to_player_id == player_id
        )
        out = StoredPairs(bought=[], gifts=[])
        for source, item_id, variant, at in await session.execute(bought.union_all(gifts)):
            (out.bought if source == "bought" else out.gifts).append(
                StoredPair(item_id, variant, at)
            )
        return out

    async def honour_facts(
        self, session: AsyncSession, player_id: uuid.UUID, *, early_before: datetime
    ) -> tuple[bool, bool]:
        """(holds a season champion row in any game, finished a game before `early_before`)."""
        champion = exists().where(
            Achievement.player_id == player_id, Achievement.code == SEASON_CHAMPION
        )
        has_title = bool((await session.execute(select(champion))).scalar())
        params = {"player_id": player_id, "before": early_before}
        early = bool((await session.execute(_PLAYED_BEFORE, params)).scalar())
        return has_title, early

    async def spent(
        self, session: AsyncSession, player_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Each player's Sikke spent (players who spent nothing are missing)."""
        stmt = (
            select(SikkeSpend.player_id, func.sum(SikkeSpend.amount))
            .where(SikkeSpend.player_id.in_(set(player_ids)))
            .group_by(SikkeSpend.player_id)
        )
        return {pid: int(total) for pid, total in await session.execute(stmt)}

    # --- Spends -----------------------------------------------------------------------------

    async def spend_by_key(
        self, session: AsyncSession, player_id: uuid.UUID, key: str
    ) -> SikkeSpend | None:
        """A player's spend with an idempotency key."""
        stmt = select(SikkeSpend).where(
            SikkeSpend.player_id == player_id, SikkeSpend.idempotency_key == key
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def add_spend(self, session: AsyncSession, spend: SikkeSpend) -> SikkeSpend:
        """Append a spend."""
        session.add(spend)
        await session.flush()
        await session.refresh(spend)
        return spend

    # --- Gifts ------------------------------------------------------------------------------

    async def gift(self, session: AsyncSession, gift_id: uuid.UUID) -> Gift | None:
        """A gift by id."""
        return await session.get(Gift, gift_id)

    async def gift_for_update(self, session: AsyncSession, gift_id: uuid.UUID) -> Gift | None:
        """A gift by id, row-locked for the rest of the transaction."""
        stmt = select(Gift).where(Gift.id == gift_id).with_for_update()
        return (await session.execute(stmt)).scalar_one_or_none()

    async def add_gift(self, session: AsyncSession, gift: Gift) -> Gift:
        """Insert a gift."""
        session.add(gift)
        await session.flush()
        await session.refresh(gift)
        return gift

    async def pending(self, session: AsyncSession, player_ids: Sequence[uuid.UUID]) -> list[Gift]:
        """Unopened gifts to any of some players, oldest first."""
        stmt = (
            select(Gift)
            .where(Gift.to_player_id.in_(set(player_ids)), Gift.opened_at.is_(None))
            .order_by(Gift.sent_at, Gift.id)
        )
        return list((await session.execute(stmt)).scalars())

    async def page_for(
        self,
        session: AsyncSession,
        player_id: uuid.UUID,
        *,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[Gift]:
        """A player's sent and received gifts, newest first, after a keyset cursor."""
        stmt = select(Gift).where(
            or_(Gift.to_player_id == player_id, Gift.from_player_id == player_id)
        )
        if before is not None:
            stmt = stmt.where(tuple_(Gift.sent_at, Gift.id) < before)
        stmt = stmt.order_by(Gift.sent_at.desc(), Gift.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    # --- Merge ------------------------------------------------------------------------------

    async def move_player(
        self, session: AsyncSession, from_id: uuid.UUID, into_id: uuid.UUID
    ) -> None:
        """A merge: spends and gifts follow the person; the kept player's look wins."""
        await session.execute(
            update(SikkeSpend).where(SikkeSpend.player_id == from_id).values(player_id=into_id)
        )
        await session.execute(
            update(Gift).where(Gift.from_player_id == from_id).values(from_player_id=into_id)
        )
        await session.execute(
            update(Gift).where(Gift.to_player_id == from_id).values(to_player_id=into_id)
        )
        kept_look = exists().where(WardrobeLook.player_id == into_id)
        await session.execute(
            update(WardrobeLook)
            .where(WardrobeLook.player_id == from_id, ~kept_look)
            .values(player_id=into_id)
        )
        await session.execute(delete(WardrobeLook).where(WardrobeLook.player_id == from_id))


wardrobe_repository = WardrobeRepository()
