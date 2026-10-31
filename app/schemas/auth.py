"""Auth models (plan §6.1)."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.players import NewPlayerIn, PlayerOut, PlayerRefIn, PlayerSummaryOut


class UserOut(CamelModel):
    """The contract's User."""

    id: uuid.UUID = Field(..., description="User id")
    email: str = Field(..., description="Login email")
    role: Literal["admin", "member"] = Field(..., description="Account role")
    player_id: uuid.UUID | None = Field(..., description="The registry player this user is")
    created_at: int = Field(..., description="Epoch ms")


class AuthOut(CamelModel):
    """`{user, player}` after login, invite or reset, and from `/auth/me`."""

    user: UserOut
    player: PlayerOut | None = Field(..., description="The user's player, once linked")


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
