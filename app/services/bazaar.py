"""The Bazaar (rewards finish §4): this week's stock, the always-on commons, and buying.

A purchase appends to `sikke_spends` under the player's row lock, so two buys can't both spend the
same balance or both buy one pair; the same `purchaseId` returns the first purchase.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SikkeSpend, User
from app.domain.time import now_utc, to_ms
from app.domain.wardrobe import (
    ITEMS,
    Item,
    Owned,
    Pair,
    Source,
    always_stock,
    base_price,
    in_stock,
    price_of,
    valid_variant,
    variants_owned,
    week_of,
    weekly_stock,
)
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.players import players_repository
from app.repositories.wardrobe import wardrobe_repository
from app.schemas.wardrobe import BazaarItemOut, BazaarOut, PurchaseIn, PurchaseOut
from app.services.wardrobe import balance_of, balance_out, holdings, owned_out, player_of
from app.settings import settings

logger = get_logger(__name__)

PURCHASE = "purchase"


def purchase_key(purchase_id: uuid.UUID) -> str:
    """A purchase's idempotency key (gifts use their own prefix, so ids never collide)."""
    return f"purchase:{purchase_id}"


def sellable_item(item_id: str, variant: int) -> Item:
    """A catalogue item that's sold in this variant; 422 `not_for_sale` or `invalid` otherwise."""
    item = ITEMS.get(item_id)
    if item is None or not item.sellable:
        raise ApiError(422, ErrorCode.NOT_FOR_SALE, "This item isn't for sale.")
    if not valid_variant(item, variant):
        raise ApiError(
            422, ErrorCode.INVALID, "No such colour.", fields={"v": "not a variant of this item"}
        )
    return item


def not_enough(balance: int, price: int) -> ApiError:
    """422 `not_enough_sikke {balance, price}`."""
    return ApiError(
        422, ErrorCode.NOT_ENOUGH_SIKKE, "Not enough Sikke.", balance=balance, price=price
    )


def already_owned() -> ApiError:
    """409 `already_owned`."""
    return ApiError(409, ErrorCode.ALREADY_OWNED, "Already owned.")


def _item_out(item: Item, pairs: set[Pair]) -> BazaarItemOut:
    return BazaarItemOut(id=item.id, price=base_price(item), owned=variants_owned(pairs, item.id))


def _bought(spend: SikkeSpend) -> Owned:
    return Owned(spend.item_id, spend.variant, Source.BOUGHT, to_ms(spend.created_at))


class BazaarService:
    """`/api/bazaar`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bazaar(self, user: User) -> BazaarOut:
        """This week's six, the always-on commons, what's owned of each and the balance."""
        player_id = player_of(user)
        held = await holdings(self.session, player_id)
        week = week_of(to_ms(now_utc()), settings.tz)
        pairs = held.pairs
        return BazaarOut(
            week=week.key,
            refreshes_at=week.refreshes_at,
            weekly=[_item_out(item, pairs) for item in weekly_stock(week)],
            always=[_item_out(item, pairs) for item in always_stock()],
            balance=balance_out(held.earned, held.spent),
        )

    async def purchase(self, user: User, body: PurchaseIn) -> PurchaseOut:
        """Buy a pair; idempotent on `purchaseId`."""
        player_id = player_of(user)
        if await players_repository.get_for_update(self.session, player_id) is None:
            raise ApiError(409, ErrorCode.NO_PLAYER, "Link a player to your account first.")
        key = purchase_key(body.purchase_id)
        first = await wardrobe_repository.spend_by_key(self.session, player_id, key)
        if first is not None:
            return PurchaseOut(
                item=owned_out(_bought(first)),
                balance=balance_out(*await balance_of(self.session, player_id)),
            )
        item = sellable_item(body.item_id, body.v)
        held = await holdings(self.session, player_id)
        owned_variants = variants_owned(held.pairs, item.id)
        if body.v in owned_variants:
            raise already_owned()
        week = week_of(to_ms(now_utc()), settings.tz)
        if not owned_variants and not in_stock(item, week):
            raise ApiError(422, ErrorCode.NOT_IN_STOCK, "Not in this week's stock.")
        price = price_of(item, body.v, owns_item=bool(owned_variants))
        if held.balance < price:
            raise not_enough(held.balance, price)
        spend = await wardrobe_repository.add_spend(
            self.session,
            SikkeSpend(
                player_id=player_id,
                kind=PURCHASE,
                item_id=item.id,
                variant=body.v,
                amount=price,
                idempotency_key=key,
            ),
        )
        await self.session.commit()
        logger.info(
            "bazaar purchase",
            extra={"player_id": str(player_id), "item": item.id, "amount": price},
        )
        return PurchaseOut(
            item=owned_out(_bought(spend)), balance=balance_out(held.earned, held.spent + price)
        )
