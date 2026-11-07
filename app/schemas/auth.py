"""Auth models (plan §6.1)."""

import uuid
from typing import Literal, Self

from pydantic import Field, model_validator

from app.domain.appearance import Theme
from app.domain.rules import player_counts
from app.schemas.common import CamelModel
from app.schemas.players import NewPlayerIn, PlayerOut, PlayerRefIn, PlayerSummaryOut


class UserOut(CamelModel):
    """The contract's User."""

    id: uuid.UUID = Field(..., description="User id")
    email: str = Field(..., description="Login email")
    role: Literal["admin", "member"] = Field(..., description="Account role")
    player_id: uuid.UUID | None = Field(..., description="The registry player this user is")
    created_at: int = Field(..., description="Epoch ms")


class SetupDefaults(CamelModel):
    """The table a player last set up, offered on every device they sign in on."""

    player_count: int = Field(
        ..., description="Players at the table; 3-7 with one deck, 3-14 with two"
    )
    decks: Literal[1, 2] = Field(..., description="Decks in play")
    direction: Literal["cw", "ccw"] = Field(..., description="Dealing direction")

    @model_validator(mode="after")
    def _seats_fit_the_decks(self) -> Self:
        allowed = player_counts(self.decks)
        if self.player_count not in allowed:
            raise ValueError(
                f"playerCount must be {allowed.start}-{allowed.stop - 1} with {self.decks} deck(s)"
            )
        return self


class PreferencesOut(CamelModel):
    """A user's own preferences, the same on every device they sign in on."""

    theme: Theme | None = Field(
        ..., description="Their theme; null follows the admin's (used only while allowed)"
    )
    setup: SetupDefaults | None = Field(..., description="Their setup defaults, if any")
    updated_at: int | None = Field(..., description="Epoch ms of the last change; null before any")


class PreferencesIn(CamelModel):
    """`PATCH /api/me/preferences`: fields sent replace the stored ones, fields left out stay."""

    theme: Theme | None = Field(None, description="A theme, or null for the admin's default")
    setup: SetupDefaults | None = Field(None, description="Setup defaults, or null to forget them")


class AuthOut(CamelModel):
    """`{user, player, preferences}` after login, invite or reset, and from `/auth/me`."""

    user: UserOut
    player: PlayerOut | None = Field(..., description="The user's player, once linked")
    preferences: PreferencesOut = Field(..., description="The user's own preferences")


class LoginIn(CamelModel):
    """`POST /api/auth/login`."""

    email: str = Field(..., max_length=254, description="Login email")
    password: str = Field(..., max_length=1024, description="Password")


class InviteInfoOut(CamelModel):
    """`GET /api/auth/invites/{token}`."""

    email: str = Field(..., description="Who the invite is for")
    player: PlayerSummaryOut | None = Field(..., description="Pre-chosen player, if any")


class AcceptInviteIn(CamelModel):
    """`POST /api/auth/invites/{token}`: a password and either an existing or a new player."""

    password: str = Field(..., max_length=1024, description="3-128 characters, no whitespace")
    player: NewPlayerIn | PlayerRefIn = Field(
        ..., union_mode="left_to_right", description="`{id}` of an existing player, or a NewPlayer"
    )


class ResetInfoOut(CamelModel):
    """`GET /api/auth/resets/{token}`."""

    email: str = Field(..., description="Whose password is being reset")


class ResetIn(CamelModel):
    """`POST /api/auth/resets/{token}`."""

    password: str = Field(..., max_length=1024, description="3-128 characters, no whitespace")


class ChangePasswordIn(CamelModel):
    """`POST /api/me/password`."""

    current: str = Field(..., max_length=1024, description="Current password")
    password: str = Field(..., max_length=1024, description="New password")
