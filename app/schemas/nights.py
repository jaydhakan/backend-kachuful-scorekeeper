"""Nights (rewards plan §7.2): the games played in one sitting, and their Wrapped story."""

import uuid
from typing import Any

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.players import PlayerSummaryOut


class NightItemOut(CamelModel):
    """One night in a player's list; the open night (not closed yet) has no id."""

    id: uuid.UUID | None = Field(..., description="Null for the open night, computed on read")
    closed: bool = Field(..., description="False only for the open night")
    started_at: int = Field(..., description="Epoch ms the night's first game started")
    ended_at: int = Field(..., description="Epoch ms its last game ended")
    players: list[PlayerSummaryOut] = Field(..., description="Everyone who sat, best night first")
    games: int = Field(..., description="Finished games of every kind")
    winner: PlayerSummaryOut | None = Field(..., description="Most wins, then edge; null if none")


class NightDetailOut(CamelModel):
    """`GET /api/nights/{id}`."""

    id: uuid.UUID = Field(..., description="Night id")
    closed: bool = Field(..., description="Whether the night is closed")
    started_at: int = Field(..., description="Epoch ms the night's first game started")
    ended_at: int = Field(..., description="Epoch ms its last game ended")
    players: list[PlayerSummaryOut] = Field(..., description="Everyone who sat, best night first")
    data: dict[str, Any] | None = Field(
        ...,
        description="The app's Night plus gameKinds, sitara, sikke {playerId: amount} and "
        "achievements [{playerId, game, code}]; null while the night is open",
    )
