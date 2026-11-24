"""The Wardrobe (rewards finish §3): the signed-in user's owned items, worn look and saved looks.

Every route answers 409 `no_player` when the account has no linked player.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import CurrentAuth, get_wardrobe_service
from app.schemas.looks import Look
from app.schemas.wardrobe import SavedLookIn, SavedLookOut, WardrobeOut
from app.services.wardrobe import WardrobeService

_WardrobeServiceDep = Annotated[WardrobeService, Depends(get_wardrobe_service)]
_Slot = Annotated[int, Path(ge=0, le=2, description="Saved look slot, 0-2")]

wardrobe_router = APIRouter(prefix="/me/wardrobe", tags=["Wardrobe"])


@wardrobe_router.get("", response_model=WardrobeOut)
async def get_wardrobe(ctx: CurrentAuth, service: _WardrobeServiceDep) -> WardrobeOut:
    """What I own (bought, gifts, level gifts, honours), my look, my saved looks and balance."""
    return await service.wardrobe(ctx.user)


@wardrobe_router.put("/look", response_model=Look)
async def put_look(body: Look, ctx: CurrentAuth, service: _WardrobeServiceDep) -> Look:
    """Wear a look; every item must be an owned pair (422 `invalid_look {slot}`)."""
    return await service.set_look(ctx.user, body)


@wardrobe_router.put("/saved/{slot}", response_model=SavedLookOut)
async def put_saved_look(
    slot: _Slot, body: SavedLookIn, ctx: CurrentAuth, service: _WardrobeServiceDep
) -> SavedLookOut:
    """Keep a look for later; it may hold items not owned yet (422 `invalid_look {slot}` for an
    unknown item, wrong slot, variant or number)."""
    return await service.save_look(ctx.user, slot, body)


@wardrobe_router.delete("/saved/{slot}", status_code=204)
async def delete_saved_look(slot: _Slot, ctx: CurrentAuth, service: _WardrobeServiceDep) -> None:
    """Clear a saved look."""
    await service.delete_saved_look(ctx.user, slot)
