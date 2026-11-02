"""Game models (plan §5.2, §6.4)."""

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class _Open(CamelModel):
    """Unknown fields are kept: the payload is stored exactly as the app sent it."""

    model_config = ConfigDict(extra="allow")


class SeatIn(_Open):
    """`players[]` in the payload."""

    id: str = Field(..., min_length=1, max_length=40, description="Game-local seat id")
    player_id: str | None = Field(default=None, max_length=64, description="Registry id")
    name: str = Field(..., max_length=100)
    emoji: str = Field(default="", max_length=32)
    color: str = Field(default="", max_length=32)


class RoundIn(_Open):
    """`rounds[]` in the payload. Numbers stay loose here; the rules check them."""

    index: int | float
    cards: int | float
    trump: str = Field(..., max_length=16)
    dealer_index: int | float
    first_player_index: int | float
    bids: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    tricks: dict[str, Any] | None = None
    hook_hits: int = Field(default=0, ge=0)
    phase: str = Field(..., max_length=16)
    done_at: int | None = Field(default=None, ge=0)


class GamePayloadIn(_Open):
    """The app's Game (schema 3) plus sync fields; checked for shape before the rules run."""

    id: str = Field(..., min_length=1, max_length=64)
    sync_id: str | None = Field(default=None, max_length=64)
    rev: int = Field(..., ge=0)
    schema_version: int | None = None
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    status: Literal["active", "finished"]
    direction: str | None = None
    first_dealer_index: int = Field(..., ge=0)
    decks: int = 1
    max_cards: int = Field(..., ge=3, le=10)
    length_mode: Literal["endless", "1cycle", "2cycles"]
    players: list[SeatIn] = Field(..., max_length=20)
    rounds: list[RoundIn] = Field(..., max_length=2000)


class SummaryOwnerOut(CamelModel):
    """Who uploaded the game. `id` is null in public responses."""

    id: uuid.UUID | None = Field(..., description="Owner's user id (null publicly)")
    name: str = Field(..., description="Owner's player display name")


class SummaryPlayerOut(CamelModel):
    """A seat in GameSummary."""

    player_id: str | None
    name: str
    emoji: str
    color: str
    photo_url: str | None = Field(..., description="The registry player's photo, or null")
    points: int
    place: int | None = Field(..., description="Null until the game has ended")


class GameSummaryOut(CamelModel):
    """The contract's GameSummary (read from `games.summary`, never from the payload)."""

    id: uuid.UUID
    owner: SummaryOwnerOut
    created_at: int = Field(..., description="Epoch ms (the app's createdAt)")
    updated_at: int = Field(..., description="Epoch ms of the last server-side change (sync)")
    ended_at: int | None = Field(..., description="Epoch ms; finished, ended, or auto-ended")
    status: Literal["active", "finished"]
    players: list[SummaryPlayerOut]
    rounds: int = Field(..., description="Done rounds")
    length_mode: str
    live: bool = Field(..., description="True while a live link is active")


class GameTombstoneOut(CamelModel):
    """A game deleted since `updatedSince`."""

    id: uuid.UUID
    deleted: Literal[True] = True
    updated_at: int = Field(..., description="Epoch ms of the deletion")


class GamesPageOut(CamelModel):
    """`GET /api/games`."""

    items: list[GameSummaryOut | GameTombstoneOut]
    next_cursor: str | None


class AchievementOut(CamelModel):
    """The contract's Achievement."""

    id: uuid.UUID
    code: str
    player_id: uuid.UUID
    game_id: uuid.UUID | None
    round_index: int | None
    period: str | None
    earned_at: int = Field(..., description="Epoch ms")
    data: dict[str, Any]


class UploadOut(CamelModel):
    """`PUT /api/games/{id}`."""

    rev: int = Field(..., description="The stored rev")
    earned: list[AchievementOut] = Field(..., description="Rows newly earned by this upload")
    remap: dict[str, str] | None = Field(
        default=None, description="Merged registry ids rewritten in the stored payload"
    )


class GameDetailOut(CamelModel):
    """`GET /api/games/{id}`."""

    game: dict[str, Any] = Field(..., description="The stored payload")
    summary: GameSummaryOut
    achievements: list[AchievementOut]


class DeletedOut(CamelModel):
    """`DELETE /api/me/games`."""

    deleted: int = Field(..., description="How many games were deleted")
