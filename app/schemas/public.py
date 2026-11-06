"""Share links and public (no login) responses (plan §6.4, §6.6). No emails, user ids or real names."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.games import AchievementOut, GameSummaryOut


class CreateLinkIn(CamelModel):
    """`POST /api/games/{id}/links`."""

    kind: Literal["live", "recap"]


class LinkOut(CamelModel):
    """A shareable link."""

    token: str = Field(..., description="22-character url-safe token")
    url: str = Field(..., description="https://play.vectanix.com/l/<token> or /r/<token>")
    qr_svg_url: str = Field(..., description="The QR code for `url`, as SVG (signed in)")
    reactions: bool = Field(default=True, description="Live: spectators may react")
    members_only: bool = Field(default=False, description="Live: only signed-in members may watch")
    expires_at: int | None = Field(
        default=None, description="Live: epoch ms it stops working (2 h after the game ends)"
    )


class LinkSettingsIn(CamelModel):
    """`PATCH /api/games/{id}/links/live`."""

    reactions: bool | None = None
    members_only: bool | None = None


REACTION_KINDS = ("clap", "fire", "laugh", "wow", "party")


class ReactionIn(CamelModel):
    """`POST /api/public/live/{token}/reactions`."""

    kind: Literal["clap", "fire", "laugh", "wow", "party"]


class LiveOut(CamelModel):
    """`GET /api/public/live/{token}` and every SSE `game` event."""

    game: dict[str, Any] = Field(..., description="The game payload")
    summary: GameSummaryOut = Field(..., description="owner.id is always null here")
    updated_at: int = Field(..., description="Epoch ms of the last upload")
    scorer_seen_at: int | None = Field(..., description="Epoch ms the scorer was last online")
    reactions: bool = Field(default=True, description="Spectators may react right now")
    recap_token: str | None = Field(
        default=None, description="The game's recap link, for the hand-off once it ends"
    )


class RecapOut(CamelModel):
    """`GET /api/public/recap/{token}`."""

    game: dict[str, Any]
    summary: GameSummaryOut
    achievements: list[AchievementOut]
