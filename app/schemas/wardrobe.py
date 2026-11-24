"""The Wardrobe, the Bazaar and gifts (rewards finish §3-§5)."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.looks import ITEM_ID_MAX_CHARS, VARIANT_MAX, Look
from app.schemas.players import PlayerSummaryOut

SAVED_LOOK_NAME_MAX_CHARS = 20


class BalanceOut(CamelModel):
    """What a player can spend: lifetime earned minus spent (negative after a recompute lowered
    `earned`; the app shows 0 and buying is blocked)."""

    earned: int = Field(..., description="Every Sikke ever earned")
    spent: int = Field(..., description="Every Sikke spent on items, bought or given")
    balance: int = Field(..., description="earned - spent")


class OwnedItemOut(CamelModel):
    """One owned (item, variant) pair."""

    id: str = Field(..., description="Catalogue id")
    v: int = Field(..., description="Colour variant")
    source: Literal["bought", "gift", "level", "honour"] = Field(..., description="How it came")
    at: int | None = Field(..., description="Epoch ms bought or sent; null when derived")


class SavedLookIn(CamelModel):
    """`PUT /api/me/wardrobe/saved/{slot}`."""

    name: str = Field(..., min_length=1, max_length=SAVED_LOOK_NAME_MAX_CHARS, description="Name")
    look: Look = Field(..., description="May hold items not owned yet")


class SavedLookOut(CamelModel):
    """A look kept for later; items not owned are dropped when it's worn."""

    name: str = Field(..., description="1-20 characters")
    look: Look = Field(..., description="The saved look")


class WardrobeOut(CamelModel):
    """`GET /api/me/wardrobe`."""

    owned: list[OwnedItemOut] = Field(..., description="Every owned pair once")
    look: Look = Field(..., description="The worn look (empty when never set)")
    saved: list[SavedLookOut | None] = Field(..., description="Always three slots")
    balance: BalanceOut = Field(..., description="What the player can spend")


class BazaarItemOut(CamelModel):
    """An item on sale."""

    id: str = Field(..., description="Catalogue id")
    price: int = Field(..., description="The item's price with variant 0")
    owned: list[int] = Field(..., description="The variants already owned")


class BazaarOut(CamelModel):
    """`GET /api/bazaar`."""

    week: str = Field(..., description="ISO week of the week's Monday, like 2026-W48")
    refreshes_at: int = Field(..., description="Epoch ms: next Monday 06:00 APP_TZ")
    weekly: list[BazaarItemOut] = Field(..., description="Hafte ka Maal: this week's six items")
    always: list[BazaarItemOut] = Field(..., description="Hamesha: every sellable common item")
    balance: BalanceOut = Field(..., description="What the player can spend")


class PurchaseIn(CamelModel):
    """`POST /api/bazaar/purchases`."""

    item_id: str = Field(..., min_length=1, max_length=ITEM_ID_MAX_CHARS, description="Item")
    v: int = Field(..., ge=0, le=VARIANT_MAX, description="Colour variant")
    purchase_id: uuid.UUID = Field(..., description="Chosen by the app; a resend returns the first")


class PurchaseOut(CamelModel):
    """What a purchase bought, and the balance now."""

    item: OwnedItemOut = Field(..., description="The bought pair")
    balance: BalanceOut = Field(..., description="The balance now")


class GiftIn(CamelModel):
    """`POST /api/gifts`."""

    item_id: str = Field(..., min_length=1, max_length=ITEM_ID_MAX_CHARS, description="Item")
    v: int = Field(..., ge=0, le=VARIANT_MAX, description="Colour variant")
    to_player_id: uuid.UUID = Field(..., description="The receiver, in the sender's circle")
    gift_id: uuid.UUID = Field(..., description="Chosen by the app; a resend returns the first")


class GiftItemOut(CamelModel):
    """The item a gift carries."""

    id: str = Field(..., description="Catalogue id")
    v: int = Field(..., description="Colour variant")


class GiftOut(CamelModel):
    """The contract's Gift."""

    id: uuid.UUID = Field(..., description="The sender's giftId")
    item: GiftItemOut = Field(..., description="What was sent")
    from_: PlayerSummaryOut = Field(..., alias="from", description="The sender")
    to: PlayerSummaryOut = Field(..., description="The receiver (owns it at once)")
    sent_at: int = Field(..., description="Epoch ms")
    opened_at: int | None = Field(..., description="Epoch ms the gift was shown; null until then")


class GiftListOut(CamelModel):
    """`GET /api/gifts/pending`."""

    items: list[GiftOut] = Field(..., description="Unopened gifts, oldest first")
