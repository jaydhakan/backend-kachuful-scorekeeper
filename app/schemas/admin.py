"""Admin models (plan §6.2)."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.auth import UserOut
from app.schemas.common import CamelModel
from app.schemas.players import PlayerOut, PlayerSummaryOut


class PendingInviteOut(CamelModel):
    """A pending invite on the users list. No URL: tokens are stored only as hashes."""

    email: str = Field(..., description="Who it is for")
    expires_at: int = Field(..., description="Epoch ms")


class AdminUserRow(CamelModel):
    """One row of `GET /api/admin/users`. Invite-only rows have `user: null`."""

    user: UserOut | None = Field(..., description="Null while only invited")
    player: PlayerOut | None = Field(..., description="Linked (or pre-chosen) player")
    active: bool = Field(..., description="False once deactivated (or not yet joined)")
    last_seen_at: int | None = Field(..., description="Epoch ms")
    invite: PendingInviteOut | None = Field(..., description="The pending invite, if any")


class CreateInviteIn(CamelModel):
    """`POST /api/admin/invites`."""

    email: str = Field(..., max_length=254, description="Who to invite")
    player_id: uuid.UUID | None = Field(default=None, description="Pre-choose their player")


class InviteLinkOut(CamelModel):
    """A fresh copyable invite link."""

    invite_url: str = Field(..., description="https://play.vectanix.com/?invite=<token>")
    expires_at: int = Field(..., description="Epoch ms (7 days)")


class ResetLinkOut(CamelModel):
    """A fresh copyable reset link."""

    reset_url: str = Field(..., description="https://play.vectanix.com/?reset=<token>")
    expires_at: int = Field(..., description="Epoch ms (24 hours)")


class UpdateUserIn(CamelModel):
    """`PATCH /api/admin/users/{id}`."""

    active: bool | None = Field(default=None, description="Deactivating revokes all sessions")


class UserEnvelopeOut(CamelModel):
    """`{user}`."""

    user: UserOut


MergeSide = Literal["from", "into"]


class MergeKeepIn(CamelModel):
    """Which player's details the kept player ends up with; a missing field keeps today's rule."""

    display_name: MergeSide | None = Field(
        default=None, description="Default into; the name not kept becomes an alias"
    )
    email: MergeSide | None = Field(
        default=None, description="Default into; the chosen side's email, else the other's"
    )
    names: MergeSide | None = Field(
        default=None,
        description="First and last name. Default into (as is); from takes the duplicate's, "
        "or keeps into's when the duplicate has neither",
    )
    photo: MergeSide | None = Field(
        default=None, description="Default into; the chosen side's photo, else the other's"
    )


class MergeIn(CamelModel):
    """`POST /api/admin/players/{id}/merge`."""

    into_id: uuid.UUID = Field(..., description="The player that remains")
    keep: MergeKeepIn | None = Field(default=None, description="Missing means today's rules")


class PlayerEnvelopeOut(CamelModel):
    """`{player}`."""

    player: PlayerOut


class DuplicatePairOut(CamelModel):
    """Two live players who may be one person."""

    a: PlayerSummaryOut
    b: PlayerSummaryOut
    reason: Literal["email", "name"] = Field(
        ..., description="email = b has a's account email; name = similar display names"
    )


class AttentionOut(CamelModel):
    """What the admin may want to fix."""

    invites_expiring_soon: int = Field(..., description="Pending invites expiring within 48 h")
    possible_duplicates: list[DuplicatePairOut] = Field(
        ..., description="At most 20, email matches first, then most similar names"
    )


class DeletionPreviewOut(CamelModel):
    """`GET /api/admin/users/{id}/deletion-preview`: what deleting the account, or its games, removes."""

    games: int = Field(..., description="Live games the user scored")
    rounds: int = Field(..., description="Done rounds across those games")
    other_players: int = Field(..., description="Other players seated in those games")
    sessions: int = Field(..., description="Signed-in browsers")
    invites: int = Field(..., description="Invites for the account's email")
    has_photo: bool = Field(..., description="Their player has a photo")
    player_in_others_games: bool = Field(
        ..., description="Their player sits in games others scored, so it is anonymised"
    )


class DeletedGamesOut(CamelModel):
    """`DELETE /api/admin/users/{id}/games`."""

    games: int = Field(..., description="Games deleted")


class OverviewOut(CamelModel):
    """`GET /api/admin/overview`."""

    users: int
    players: int
    games: int
    rounds: int
    last_backup_at: int | None = Field(..., description="Epoch ms of the last finished backup")
    attention: AttentionOut = Field(..., description="Things that need the admin's attention")
