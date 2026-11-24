"""The Wardrobe (rewards finish §3): what a player owns, the look they wear and their saved looks.

Ownership is derived on read: purchases and gifts received (stored) plus level gifts and honours.
`holdings` is shared with the Bazaar and gifts, which run it under the player's row lock.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.sikke import level_of
from app.domain.time import from_ms, to_ms
from app.domain.wardrobe import (
    Owned,
    OwnershipFacts,
    Pair,
    look_problem,
    owned_items,
    owned_look,
    owned_pairs,
    pehle_waale_cutoff_ms,
)
from app.exceptions import ApiError, ErrorCode
from app.repositories.app_state import app_state_repository
from app.repositories.players import players_repository
from app.repositories.sikke import sikke_repository
from app.repositories.wardrobe import StoredPair, wardrobe_repository
from app.schemas.looks import Look
from app.schemas.wardrobe import (
    BalanceOut,
    OwnedItemOut,
    SavedLookIn,
    SavedLookOut,
    WardrobeOut,
)
from app.settings import settings

SAVED_SLOTS = 3


@dataclass(frozen=True)
class Holdings:
    """A player's owned pairs and Sikke balance."""

    owned: list[Owned]
    earned: int
    spent: int

    @property
    def pairs(self) -> set[Pair]:
        """The owned (item, variant) pairs."""
        return owned_pairs(self.owned)

    @property
    def balance(self) -> int:
        """Earned minus spent; negative when a recompute lowered `earned` below `spent`."""
        return self.earned - self.spent


def _facts(pairs: list[StoredPair]) -> tuple[tuple[str, int, int], ...]:
    return tuple((p.item_id, p.variant, to_ms(p.at)) for p in pairs)


async def balance_of(session: AsyncSession, player_id: uuid.UUID) -> tuple[int, int]:
    """(lifetime earned, spent) of one player."""
    earned = (await sikke_repository.lifetimes(session, [player_id])).get(player_id, 0)
    spent = (await wardrobe_repository.spent(session, [player_id])).get(player_id, 0)
    return earned, spent


async def holdings(session: AsyncSession, player_id: uuid.UUID) -> Holdings:
    """What a player owns now and can spend (four queries)."""
    stored = await wardrobe_repository.stored_pairs(session, player_id)
    champion, early = await wardrobe_repository.honour_facts(
        session, player_id, early_before=from_ms(pehle_waale_cutoff_ms(settings.tz))
    )
    earned, spent = await balance_of(session, player_id)
    facts = OwnershipFacts(
        bought=_facts(stored.bought),
        gifts=_facts(stored.gifts),
        lifetime=earned,
        champion=champion,
        early=early,
    )
    return Holdings(owned_items(facts, level_of(earned).n), earned, spent)


def balance_out(earned: int, spent: int) -> BalanceOut:
    """A balance on the wire."""
    return BalanceOut(earned=earned, spent=spent, balance=earned - spent)


def owned_out(owned: Owned) -> OwnedItemOut:
    """An owned pair on the wire."""
    return OwnedItemOut(id=owned.item_id, v=owned.variant, source=owned.source.value, at=owned.at)


def player_of(user: User) -> uuid.UUID:
    """The user's linked player; 409 `no_player` without one."""
    if user.player_id is None:
        raise ApiError(409, ErrorCode.NO_PLAYER, "Link a player to your account first.")
    return user.player_id


def invalid_look(slot: str) -> ApiError:
    """422 `invalid_look {slot}`."""
    return ApiError(422, ErrorCode.INVALID_LOOK, f"This look can't wear its {slot}.", slot=slot)


def _look_data(look: Look) -> dict[str, Any]:
    data: dict[str, Any] = look.model_dump(mode="json")
    return data


def _saved(rows: dict[int, SavedLookOut]) -> list[SavedLookOut | None]:
    return [rows.get(slot) for slot in range(SAVED_SLOTS)]


class WardrobeService:
    """`/api/me/wardrobe`: owned items, the worn look and saved looks of the user's player."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def wardrobe(self, user: User) -> WardrobeOut:
        """Everything the Wardrobe page shows."""
        player_id = player_of(user)
        held = await holdings(self.session, player_id)
        look = await wardrobe_repository.look(self.session, player_id) or {}
        saved = {
            row.slot: SavedLookOut(name=row.name, look=Look.model_validate(row.look))
            for row in await wardrobe_repository.saved_looks(self.session, player_id)
        }
        return WardrobeOut(
            owned=[owned_out(o) for o in held.owned],
            look=Look.model_validate(owned_look(look, held.pairs)),
            saved=_saved(saved),
            balance=balance_out(held.earned, held.spent),
        )

    async def set_look(self, user: User, look: Look) -> Look:
        """Wear a look of owned pairs; 422 `invalid_look {slot}` otherwise."""
        player_id = player_of(user)
        player = await players_repository.get_for_update(self.session, player_id)
        if player is None:
            raise ApiError(409, ErrorCode.NO_PLAYER, "Link a player to your account first.")
        data = _look_data(look)
        problem = look_problem(data, (await holdings(self.session, player_id)).pairs)
        if problem is not None:
            raise invalid_look(problem)
        await wardrobe_repository.save_look(self.session, player_id, data)
        # Every embedded player carries the look: registry sync picks it up like a photo.
        await players_repository.touch(self.session, player)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        return look

    async def save_look(self, user: User, slot: int, body: SavedLookIn) -> SavedLookOut:
        """Keep a look in a slot (0-2); it may hold items not owned yet, but only real ones."""
        player_id = player_of(user)
        data = _look_data(body.look)
        problem = look_problem(data, None)
        if problem is not None:
            raise invalid_look(problem)
        await wardrobe_repository.save_saved_look(
            self.session, player_id, slot=slot, name=body.name, look=data
        )
        await self.session.commit()
        return SavedLookOut(name=body.name, look=body.look)

    async def delete_saved_look(self, user: User, slot: int) -> None:
        """Clear a saved look (a no-op when the slot is empty)."""
        player_id = player_of(user)
        await wardrobe_repository.delete_saved_look(self.session, player_id, slot)
        await self.session.commit()
