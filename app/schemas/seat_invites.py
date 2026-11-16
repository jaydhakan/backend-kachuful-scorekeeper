"""Seat invites and the circle they are sent to (backend-requests 2026-11-16, §1-§2)."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel

SeatInviteKind = Literal["ishaara", "spades-online"]
SeatInviteStatus = Literal["pending", "accepted", "declined", "cancelled", "expired"]


class CircleMemberOut(CamelModel):
    """Someone in my circle with an account: who I can invite to a seat."""

    user_id: uuid.UUID = Field(..., description="Their account")
    player_id: uuid.UUID = Field(..., description="Their registry player")
    name: str = Field(..., description="The player's display name")
    emoji: str = Field(..., description="The player's emoji")
    color: str = Field(..., description="The player's colour (hex)")
    photo_url: str | None = Field(..., description="/api/players/{id}/photo?v={rev}, or null")
    last_seen_at: int | None = Field(
        ..., description="Epoch ms of their last activity or stream ping; online if < 60 s ago"
    )
    last_played_at: int | None = Field(..., description="Epoch ms of the player's last game")


class CircleOut(CamelModel):
    """`GET /api/me/circle`: invitable members, last played first."""

    items: list[CircleMemberOut] = Field(..., description="Members, newest lastPlayedAt first")


class SeatInviteFromOut(CamelModel):
    """The host who sent an invite."""

    user_id: uuid.UUID = Field(..., description="The host's account")
    name: str = Field(..., description="The host's player name (empty without a player)")
    emoji: str = Field(..., description="The host's player emoji")
    color: str = Field(..., description="The host's player colour (hex)")
    photo_url: str | None = Field(..., description="The host's player photo, or null")


class SeatInviteTableOut(CamelModel):
    """The table an invite is for."""

    name: str = Field(..., description="The table's name; Ishaara: the host's name + \"'s game\"")
    seats_taken: list[int] = Field(..., description="Seats already taken (Spades); Ishaara: []")


class SeatInvitePartnerOut(CamelModel):
    """Who the invitee would partner (Spades)."""

    name: str = Field(..., description="The partner's name")


class SeatInviteOut(CamelModel):
    """A seat invite as its invitee sees it."""

    id: uuid.UUID = Field(..., description="Invite id")
    kind: SeatInviteKind = Field(..., description="The game's kind")
    game_id: uuid.UUID = Field(..., description="The game")
    seat: str = Field(..., description='"0"-"3" (Spades) or "clue:sun" / "clue:moon" (Ishaara)')
    status: SeatInviteStatus = Field(..., description="Expiry is worked out on read")
    created_at: int = Field(..., description="Epoch ms; the app hides it 30 minutes later")
    from_: SeatInviteFromOut = Field(..., alias="from", description="The host")
    table: SeatInviteTableOut = Field(..., description="The table")
    partner: SeatInvitePartnerOut | None = Field(..., description="Spades only; else null")


class SeatInvitesOut(CamelModel):
    """`GET /api/seat-invites`: my pending invites."""

    items: list[SeatInviteOut] = Field(..., description="Pending invites, newest first")
    server_now: int = Field(..., description="The server clock, epoch ms")


class SeatInviteIn(CamelModel):
    """A host's invite to one seat (each game's `POST .../seat-invites`)."""

    user_id: uuid.UUID = Field(..., description="The invited account")
    seat: str = Field(..., min_length=1, max_length=20, description="The seat offered")


class SeatInviteGoneOut(CamelModel):
    """Event `seat-invite-gone` to the invitee: an invite is no longer pending."""

    id: uuid.UUID = Field(..., description="Invite id")
    status: SeatInviteStatus = Field(..., description="Its final status")


class SeatInviteUpdateOut(CamelModel):
    """Event `seat-invite-update` to the host: an invite they sent changed."""

    id: uuid.UUID = Field(..., description="Invite id")
    status: SeatInviteStatus = Field(..., description="Its status now")
    game_id: uuid.UUID = Field(..., description="The game")
    seat: str = Field(..., description="The seat")
