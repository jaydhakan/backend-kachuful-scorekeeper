"""Seat claims (contract change 2026-11-06)."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel


class ClaimIn(CamelModel):
    """`POST /api/games/{id}/claims`."""

    player_id: uuid.UUID = Field(..., description="The seat's player, which has no account")


class ClaimPlayerOut(CamelModel):
    """The claimed player."""

    id: uuid.UUID
    display_name: str


class ClaimantOut(CamelModel):
    """Who is asking."""

    user_id: uuid.UUID
    player_id: uuid.UUID | None
    display_name: str


class ClaimOut(CamelModel):
    """A seat claim."""

    id: uuid.UUID
    game_id: uuid.UUID
    status: Literal["pending", "approved", "rejected"]
    created_at: int = Field(..., description="Epoch ms")
    player: ClaimPlayerOut
    claimant: ClaimantOut


class ClaimsOut(CamelModel):
    """`GET /api/claims`."""

    items: list[ClaimOut]
