"""Kaali Teeri models: the uploaded KtGame's shape, summaries, boards and stats."""

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel
from app.schemas.games import GameTombstoneOut, SeatIn, SummaryOwnerOut
from app.schemas.players import PlayerSummaryOut

Number = int | float


class _Open(CamelModel):
    """Unknown fields are kept: the payload is stored exactly as the app sent it."""

    model_config = ConfigDict(extra="allow")


class KtRulesIn(_Open):
    """`rules` in the payload; the domain checks the values."""

    decks: Number
    partners: Number
    min_bid: Number
    step: Number
    length: str = Field(..., max_length=16)


class KtCallIn(_Open):
    """One auction call: `bid`, `pass` or `forced` (which keys are present matters)."""

    player_id: str = Field(..., max_length=40)
    bid: Number | None = None
    pass_: bool | None = Field(default=None, alias="pass")
    forced: Number | None = None


class KtCardIn(_Open):
    """A called card."""

    rank: str = Field(..., max_length=3)
    suit: str = Field(..., max_length=10)
    copy_: int | str | None = Field(default=None, alias="copy", description="1, 2 or 'other'")


class KtHandIn(_Open):
    """`hands[]` in the payload. Numbers stay loose here; the rules check them."""

    index: Number
    dealer_index: Number
    phase: str = Field(..., max_length=16)
    mode: str = Field(..., max_length=16)
    calls: list[KtCallIn] = Field(..., max_length=200)
    bidder: str | None = Field(..., max_length=40)
    bid: Number | None
    trump: str | None = Field(..., max_length=16)
    called: list[KtCardIn] = Field(..., max_length=10)
    played_by: list[str | None] = Field(..., max_length=10)
    team_points: Number | None
    counted_by: str | None = Field(..., max_length=16)
    done_at: int | None = Field(default=None, ge=0)


class KtGameIn(_Open):
    """The app's KtGame (schema 1); checked for shape before the rules run."""

    kind: Literal["kaali-teeri"]
    schema_version: int | None = None
    id: str = Field(..., min_length=1, max_length=64)
    sync_id: str = Field(..., min_length=1, max_length=64)
    rev: int = Field(..., ge=0)
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    status: Literal["active", "finished"]
    players: list[SeatIn] = Field(..., max_length=20)
    first_dealer_index: Number
    direction: str | None = Field(default=None, max_length=8)
    rules: KtRulesIn
    hands: list[KtHandIn] = Field(..., max_length=2000)


class KtSummaryPlayerOut(CamelModel):
    """A seat in a Kaali Teeri summary."""

    player_id: str | None
    name: str
    emoji: str
    color: str
    photo_url: str | None = Field(..., description="The registry player's photo, or null")
    points: int
    place: int | None = Field(..., description="Null until the game is finished")


class KtGameSummaryOut(CamelModel):
    """A Kaali Teeri game in lists (read from `games.summary`, never from the payload)."""

    id: uuid.UUID
    kind: Literal["kaali-teeri"] = "kaali-teeri"
    owner: SummaryOwnerOut
    created_at: int = Field(..., description="Epoch ms (the app's createdAt)")
    finished_at: int | None = Field(..., description="Epoch ms, once finished")
    updated_at: int = Field(..., description="Epoch ms of the last server-side change (sync)")
    status: Literal["active", "finished"]
    hands: int = Field(..., description="Done hands")
    length: str = Field(..., description="endless or to1000")
    players: list[KtSummaryPlayerOut]
    live: bool = Field(..., description="True while a live link is active")


class KtGamesPageOut(CamelModel):
    """`GET /api/kaali-teeri/games`."""

    items: list[KtGameSummaryOut | GameTombstoneOut]
    next_cursor: str | None


class KtGameDetailOut(CamelModel):
    """`GET /api/kaali-teeri/games/{id}`."""

    game: dict[str, Any] = Field(..., description="The stored payload")
    summary: KtGameSummaryOut


class KtBootstrapOut(CamelModel):
    """`GET /api/kaali-teeri/bootstrap`: players come from `GET /api/bootstrap`."""

    my_games: list[KtGameSummaryOut] = Field(..., description="Live games the caller scored")
    data_version: int


class KtLeaderboardRowOut(CamelModel):
    """One player on the Kaali Teeri board."""

    rank: int | None = Field(..., description="Null when unranked (made % with no hands bid)")
    player: PlayerSummaryOut
    value: float = Field(..., description="The sorted value: points, wins or made % (0-1)")
    points: int
    games: int = Field(..., description="Games started in the period")
    wins: int = Field(..., description="Games won that finished in the period")
    hands_bid: int
    bids_made: int
    made_rate: float | None = Field(..., description="bidsMade / handsBid, null with no bids")


class KtLeaderboardOut(CamelModel):
    """`GET /api/kaali-teeri/leaderboard`."""

    period: Literal["today", "week", "season", "all"]
    sort: Literal["points", "wins", "made"]
    rows: list[KtLeaderboardRowOut]
    generated_at: int = Field(..., description="Epoch ms")


class KtBestGameOut(CamelModel):
    """The best finished game by points."""

    game_id: str
    points: int


class KtMostPartnerOut(CamelModel):
    """The player most often on the same bidding team."""

    player_id: str
    hands: int
    made: int


class KtStreakOut(CamelModel):
    """Made bids in a row."""

    current: int
    best: int


class KtPlayerStatsOut(CamelModel):
    """`GET /api/kaali-teeri/players/{id}/stats`: the app's playerStats fields."""

    games: int
    wins: int
    win_rate: float | None
    avg_place: float | None
    points: int
    best_game: KtBestGameOut | None
    hands_bid: int
    bids_made: int
    made_rate: float | None
    avg_bid: float | None
    highest_made: int | None
    hands_lost: int
    sweeps: int
    times_partner: int
    partner_made: int
    partner_win_rate: float | None
    double_slots: int
    most_partner: KtMostPartnerOut | None
    made_streak: KtStreakOut


class KtHeadToHeadOut(CamelModel):
    """`GET /api/kaali-teeri/players/{a}/vs/{b}`: the app's headToHead fields."""

    a: PlayerSummaryOut
    b: PlayerSummaryOut
    games: int = Field(..., description="Games both sat in with a hand played")
    a_ahead: int = Field(..., description="Finished games where a placed better")
    b_ahead: int
    level: int
    together: int = Field(..., description="Hands on the same bidding team")
    together_made: int
    a_bid_against_b: int
    a_made_against_b: int
    b_bid_against_a: int
    b_made_against_a: int
