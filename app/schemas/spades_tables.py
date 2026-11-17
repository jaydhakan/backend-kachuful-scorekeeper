"""Spades online tables (kind `spades-online`): the server runs the game (backend-requests §4).

The table on the wire is the domain's SpPublic as one viewer may see it (`viewFor`); hands are
only ever in `me.hand`, and only the viewer's own.
"""

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel

Number = int | float
SpStatus = Literal["lobby", "playing", "finished", "cancelled"]
SpPhase = Literal["draw", "deal", "bidding", "playing", "handEnd"]
SpActionType = Literal[
    "ready",
    "unready",
    "shufflePartners",
    "drawForDeal",
    "deal",
    "look",
    "blindNil",
    "bid",
    "play",
    "timeout",
    "react",
    "replace",
    "endGame",
    "drawGame",
    "cancelGame",
    "rematch",
]


class SpRulesIn(CamelModel):
    """SpRules; the domain checks the values (`invalid_action {reason: 'bad-rules'}`)."""

    target: Number | None = Field(..., description="50-1000 in tens; null = Endless")
    timer_sec: Number | None = Field(..., description="30, 60 or null (off)")
    nil: bool = Field(..., description="Nil bids allowed")
    blind_nil: bool = Field(..., description="Blind Nil allowed (needs nil)")
    min_team_bid: Number | None = Field(..., description="4 when on, else null")
    mercy: Number | None = Field(..., description="-200 when on, else null")


class CreateTableIn(CamelModel):
    """`POST /api/spades/tables`."""

    id: uuid.UUID = Field(..., description="The new table's id, made by the app")
    name: str | None = Field(
        default=None, max_length=100, description='Defaults to "<host>\'s table"; 1-40 chars'
    )
    rules: SpRulesIn


class JoinTableIn(CamelModel):
    """`POST /api/spades/tables/{id}/join`."""

    seat: Number | None = Field(default=None, description="0-3; the lowest free seat if omitted")


class TableRulesIn(CamelModel):
    """`PATCH /api/spades/tables/{id}/rules` (host, lobby)."""

    rules: SpRulesIn
    name: str | None = Field(default=None, max_length=100, description="1-40 chars once trimmed")


class SpActionIn(CamelModel):
    """`POST /api/spades/tables/{id}/actions`: the domain's Action plus `actionId` and `rev`.

    The action's own fields (`bid`, `card`, `seat`, `emoji`, `id`, `swap`) pass to the rules as sent.
    """

    model_config = ConfigDict(extra="allow")

    action_id: uuid.UUID = Field(..., description="Idempotency key: a repeat returns the view")
    rev: int = Field(..., ge=0, description="The table rev the sender saw")
    type: SpActionType = Field(..., description="The action")

    def action(self) -> dict[str, Any]:
        """The action as the domain reads it (camelCase, without actionId and rev)."""
        extra = dict(self.model_extra or {})
        return {**extra, "type": self.type}


class CardOut(CamelModel):
    """A card."""

    rank: str = Field(..., description='"A", "K", "Q", "J", "10" ... "2"')
    suit: str = Field(..., description="spades, hearts, diamonds or clubs")


class SpMeOut(CamelModel):
    """Who is looking."""

    seat: int | None = Field(..., description="The viewer's seat, or null")
    host: bool = Field(..., description="The viewer holds the host's seat")
    hand: list[CardOut] | None = Field(
        ..., description="The viewer's own cards once they may see them (Blind Nil: after look)"
    )


class SpSeatInviteOut(CamelModel):
    """A seat's pending seat invite, as the lobby shows the invited chair."""

    id: uuid.UUID = Field(..., description="The seat invite (the host cancels it by this id)")
    user_id: uuid.UUID = Field(..., description="The invited account")
    name: str = Field(..., description="The invitee's player name")
    emoji: str = Field(..., description="The invitee's emoji")
    color: str = Field(..., description="The invitee's colour (hex)")
    photo_url: str | None = Field(..., description="The invitee's photo, or null")


class SpViewOut(CamelModel):
    """SpView: the table as this viewer may see it. Also each SSE `state` frame."""

    table: dict[str, Any] = Field(..., description="SpPublic through viewFor; never a hand")
    me: SpMeOut
    server_now: int = Field(..., description="The server clock, epoch ms")
    invites: list[SpSeatInviteOut | None] = Field(
        ..., description="By seat (4): the seat's live pending invite, else null"
    )


class TableRefOut(CamelModel):
    """`GET /api/spades/tables/by-code/{code}`."""

    id: uuid.UUID = Field(..., description="The open table with this code")


class SpSummarySeatOut(CamelModel):
    """A seat in a table summary (no account ids)."""

    player_id: str = Field(..., description="Registry player")
    name: str
    emoji: str
    color: str
    photo_url: str | None = None


class SpTableSummaryOut(CamelModel):
    """An online table in lists (read from `games.summary`, never the payload)."""

    id: uuid.UUID
    kind: Literal["spades-online"] = "spades-online"
    code: str = Field(..., description="The 4-letter join code")
    name: str
    status: SpStatus
    phase: SpPhase
    paused: Literal["replace"] | None
    rules: dict[str, Any] = Field(..., description="SpRules")
    host: int = Field(..., description="The host's seat")
    seats: list[SpSummarySeatOut | None] = Field(..., description="By seat (4); null when empty")
    scores: dict[str, int] = Field(..., description="{a, b}: totals over finished hands")
    hands: int = Field(..., description="Finished hands")
    result: dict[str, str] | None = Field(..., description="SpResult once finished, else null")
    created_at: int
    updated_at: int = Field(..., description="Epoch ms of the table's last change")
    finished_at: int | None
    my_seat: int | None = Field(..., description="The caller's seat now, or null")
    is_host: bool = Field(..., description="The caller hosts this table")


class SpTableFeedOut(CamelModel):
    """An online table in the activity feed: its summary without the join code.

    Any signed-in reader sees the feed, and a hand's achievements are posted while the table is
    still open, so the code (which lets anyone take an empty chair) never goes there.
    """

    id: uuid.UUID = Field(..., description="Table id")
    kind: Literal["spades-online"] = Field("spades-online", description="Always spades-online")
    name: str = Field(..., description="The table's name")
    status: SpStatus = Field(..., description="Table status")
    phase: SpPhase = Field(..., description="Current phase")
    paused: Literal["replace"] | None = Field(..., description="Paused to replace a seat")
    rules: dict[str, Any] = Field(..., description="SpRules")
    host: int = Field(..., description="The host's seat")
    seats: list[SpSummarySeatOut | None] = Field(..., description="By seat (4); null when empty")
    scores: dict[str, int] = Field(..., description="{a, b}: totals over finished hands")
    hands: int = Field(..., description="Finished hands")
    result: dict[str, str] | None = Field(..., description="SpResult once finished, else null")
    created_at: int = Field(..., description="Epoch ms")
    updated_at: int = Field(..., description="Epoch ms of the table's last change")
    finished_at: int | None = Field(..., description="Epoch ms, once finished")
    my_seat: int | None = Field(..., description="The reader's seat now, or null")
    is_host: bool = Field(..., description="The reader hosts this table")


class SpTablesPageOut(CamelModel):
    """`GET /api/spades/tables`."""

    items: list[SpTableSummaryOut]
    next_cursor: str | None
