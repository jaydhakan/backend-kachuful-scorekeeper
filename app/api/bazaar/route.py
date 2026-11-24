"""The Bazaar (rewards finish §4): this week's stock, the always-on commons, and buying."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentAuth, get_bazaar_service
from app.schemas.wardrobe import BazaarOut, PurchaseIn, PurchaseOut
from app.services.bazaar import BazaarService

_BazaarServiceDep = Annotated[BazaarService, Depends(get_bazaar_service)]

bazaar_router = APIRouter(prefix="/bazaar", tags=["Bazaar"])


@bazaar_router.get("", response_model=BazaarOut)
async def get_bazaar(ctx: CurrentAuth, service: _BazaarServiceDep) -> BazaarOut:
    """This week's six items (Hafte ka Maal), every common (Hamesha), what I own and my balance.
    409 `no_player` without a linked player."""
    return await service.bazaar(ctx.user)


@bazaar_router.post("/purchases", response_model=PurchaseOut)
async def create_purchase(
    body: PurchaseIn, ctx: CurrentAuth, service: _BazaarServiceDep
) -> PurchaseOut:
    """Buy an item in a colour; the same `purchaseId` returns the first purchase. Errors:
    422 `not_for_sale`, 422 `not_in_stock`, 409 `already_owned`, 422 `not_enough_sikke
    {balance, price}`, 409 `no_player`."""
    return await service.purchase(ctx.user, body)
