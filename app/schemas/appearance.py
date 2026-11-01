"""Appearance settings models (contract change 2026-11-01)."""

from pydantic import Field, StrictBool

from app.domain.appearance import Theme
from app.schemas.common import CamelModel


class AppearanceOut(CamelModel):
    """`GET /api/settings/appearance`: the theme everyone sees."""

    theme: Theme = Field(..., description="The theme for everyone (and the default for players)")
    allow_user_override: bool = Field(..., description="Whether players may pick their own theme")
    updated_at: int | None = Field(..., description="Epoch ms of the last change; null before any")
    updated_by: str | None = Field(
        ...,
        description="The player name of the admin who last changed it; null before any change",
    )


class AppearanceIn(CamelModel):
    """`PUT /api/admin/settings/appearance`."""

    theme: Theme = Field(..., description="The theme for everyone")
    allow_user_override: StrictBool = Field(
        ..., description="Whether players may pick their own theme"
    )
