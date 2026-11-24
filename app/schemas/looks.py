"""A worn look (rewards finish §3): shared by the Wardrobe and every embedded player."""

from typing import Any

from pydantic import ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from app.schemas.common import CamelModel

ITEM_ID_MAX_CHARS = 40
VARIANT_MAX = 31


class _Sparse(CamelModel):
    """Optional fields that are unset or null are left out on the wire (`number?`, `outfit?`)."""

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _drop_nulls(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class LookItem(_Sparse):
    """One worn item: its id and colour variant (0 when it has one)."""

    id: str = Field(..., min_length=1, max_length=ITEM_ID_MAX_CHARS, description="Catalogue id")
    v: int = Field(..., ge=0, le=VARIANT_MAX, description="Colour variant")
    number: int | None = Field(
        default=None, ge=0, le=99, description="Only on `outfit` when `id` is `jersey`"
    )


class Look(_Sparse):
    """The worn set; every slot optional."""

    outfit: LookItem | None = Field(default=None, description="Outfit")
    headwear: LookItem | None = Field(default=None, description="Headwear")
    neckwear: LookItem | None = Field(default=None, description="Neckwear")
    frame: LookItem | None = Field(default=None, description="Avatar frame")
    aura: LookItem | None = Field(default=None, description="Aura")
    badge: LookItem | None = Field(default=None, description="Badge")
    title: LookItem | None = Field(default=None, description="Title")
