"""Spades table mode (kind `spades`): the uploaded SpGame's shape, summaries, and the boards and
stats that read both Spades kinds (backend-requests §5)."""

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel
from app.schemas.games import GameTombstoneOut, SeatIn, SummaryOwnerOut
from app.schemas.players import PlayerSummaryOut

Number = int | float
SpTeam = Literal["a", "b"]


class _Open(CamelModel):
    """Unknown fields are kept: the payload is stored exactly as the app sent it."""

    model_config = ConfigDict(extra="allow")


class SpGameRulesIn(_Open):
    """`rules` in the payload; the domain checks the values (`invalid_game {reason: 'rules'}`)."""

    target: Number | None = Field(..., description="50-1000 in tens; null = Endless")
    timer_sec: Number | None = Field(default=None, description="Online only; ignored here")
    nil: bool = Field(..., description="Nil bids allowed")
    blind_nil: bool = Field(..., description="Blind Nil allowed (needs nil)")
    min_team_bid: Number | None = Field(..., description="4 when on, else null")
    mercy: Number | None = Field(..., description="-200 when on, else null")


class SpHandIn(_Open):
    """`hands[]` in the payload. Numbers stay loose here; the rules check them."""

    index: Number = Field(..., description="0-based hand number")
    dealer: Number = Field(..., description="The dealer's seat")
    bids: list[Number | None] = Field(..., max_length=8, description="By seat; 0 is Nil")
    blind: list[Any] = Field(..., max_length=8, description="By seat: a Blind Nil")
    team_tricks: list[Number] | None = Field(..., max_length=4, description="[team a, team b]")
    nil_tricks: list[Number | None] = Field(
        ..., max_length=8, description="By seat: tricks a Nil bidder took"
    )
    phase: str = Field(..., max_length=16, description="bidding, tricks or done")
    done_at: int | None = Field(default=None, ge=0, description="Epoch ms the hand was scored")


class SpResultIn(_Open):
    """`result` in the payload; checked against the hands by the domain."""

    winner: str = Field(..., max_length=8, description="a, b or draw")
    ended_by: str = Field(..., max_length=8, description="target, mercy, end or draw")


class SpGameIn(_Open):
    """The app's SpGame (schema 1); checked for shape before the rules run."""

    kind: Literal["spades"] = Field(..., description="Always spades (table mode)")
    schema_version: int | None = Field(default=None, description="1")
    id: str = Field(..., min_length=1, max_length=64, description="The app's local id")
    sync_id: str = Field(..., min_length=1, max_length=64, description="Must match the path id")
    rev: int = Field(..., ge=0, description="Bumped by the app on every change")
    created_at: int = Field(..., ge=0, description="Epoch ms")
    updated_at: int = Field(..., ge=0, description="Epoch ms")
    finished_at: int | None = Field(default=None, ge=0, description="Epoch ms, once ended")
    status: Literal["active", "finished", "cancelled"] = Field(..., description="Game status")
    players: list[SeatIn] = Field(..., max_length=8, description="In seat order (4)")
    first_dealer_index: Number = Field(..., description="The first hand's dealer seat")
    direction: str | None = Field(default=None, max_length=8, description="cw or ccw (layout)")
    rules: SpGameRulesIn
    hands: list[SpHandIn] = Field(..., max_length=2000, description="Every hand, in order")
    result: SpResultIn | None = Field(default=None, description="Once finished")


class SpResultOut(CamelModel):
    """How a finished game ended."""

    winner: Literal["a", "b", "draw"] = Field(..., description="The winning team, or draw")
    ended_by: Literal["target", "mercy", "end", "draw"] = Field(
        ..., description="Target reached, mercy rule, the host's End game, or a called draw"
    )


class SpScoresOut(CamelModel):
    """Both teams' totals over the done hands (bag penalties included)."""

    a: int = Field(..., description="Team a (seats 0 and 2)")
    b: int = Field(..., description="Team b (seats 1 and 3)")


class SpSummaryPlayerOut(CamelModel):
    """A seat in a table-mode summary."""

    player_id: str | None = Field(..., description="Registry player")
    name: str = Field(..., description="Name at the table")
    emoji: str = Field(..., description="Avatar emoji")
    color: str = Field(..., description="Avatar colour (hex)")
    photo_url: str | None = Field(..., description="The registry player's photo, or null")
    team: SpTeam = Field(..., description="a for seats 0 and 2, b for seats 1 and 3")


class SpGameSummaryOut(CamelModel):
    """A table-mode game in lists (read from `games.summary`, never from the payload)."""

    id: uuid.UUID = Field(..., description="The game's sync id")
    kind: Literal["spades"] = Field(default="spades", description="Table mode")
    owner: SummaryOwnerOut
    created_at: int = Field(..., description="Epoch ms (the app's createdAt)")
    finished_at: int | None = Field(..., description="Epoch ms, once finished or cancelled")
    updated_at: int = Field(..., description="Epoch ms of the last server-side change (sync)")
    status: Literal["active", "finished", "cancelled"] = Field(..., description="Game status")
    hands: int = Field(..., description="Done hands")
    target: int | None = Field(..., description="rules.target; null = Endless")
    players: list[SpSummaryPlayerOut] = Field(..., description="In seat order")
    scores: SpScoresOut
    result: SpResultOut | None = Field(..., description="Null unless finished (cancelled: null)")
    live: bool = Field(..., description="True while a live link is active")


class SpGamesPageOut(CamelModel):
    """`GET /api/spades/games`."""

    items: list[SpGameSummaryOut | GameTombstoneOut] = Field(..., description="This page")
    next_cursor: str | None = Field(..., description="Pass as ?cursor= for the next page")


class SpGameDetailOut(CamelModel):
    """`GET /api/spades/games/{id}`."""

    game: dict[str, Any] = Field(..., description="The stored payload")
    summary: SpGameSummaryOut


class SpBootstrapOut(CamelModel):
    """`GET /api/spades/bootstrap`: players come from `GET /api/bootstrap`."""

    my_games: list[SpGameSummaryOut] = Field(..., description="Live games the caller scored")
    data_version: int = Field(..., description="Bumps on every change to shared data")


class _Totals(CamelModel):
    """A player's Spades totals over both kinds (a draw is played, not won)."""

    games: int = Field(..., description="Finished games with a result (cancelled never count)")
    wins: int = Field(..., description="Games won")
    draws: int = Field(..., description="Games drawn")
    losses: int = Field(..., description="Games lost")
    win_rate: float | None = Field(..., description="wins / games, null with no games")
    hands: int = Field(..., description="Hands played (a replaced player's included)")
    bids: int = Field(..., description="Non-Nil bids")
    bids_made: int = Field(..., description="Non-Nil bids whose team made its contract")
    made_rate: float | None = Field(..., description="bidsMade / bids, null with no bids")
    nils: int = Field(..., description="Nil bids (not blind)")
    nils_made: int = Field(..., description="Nil bids made")
    nil_rate: float | None = Field(..., description="nilsMade / nils, null with none")
    blind_nils: int = Field(..., description="Blind Nil bids")
    blind_nils_made: int = Field(..., description="Blind Nil bids made")
    blind_nil_rate: float | None = Field(..., description="blindNilsMade / blindNils")
    bags: int = Field(..., description="The player's team's bags over their hands")
    bags_per_hand: float | None = Field(..., description="bags / hands, null with no hands")
    sets: int = Field(..., description="Non-Nil bids whose team was set")


class SpLeaderboardRowOut(_Totals):
    """One player on the Spades board."""

    rank: int | None = Field(
        ..., description="Null when unranked: rate needs 3 games, made needs a bid"
    )
    player: PlayerSummaryOut
    value: float = Field(..., description="The sorted value: wins, win rate or made rate (0-1)")


class SpPairOut(CamelModel):
    """Two partners' record together."""

    a: PlayerSummaryOut
    b: PlayerSummaryOut
    games: int = Field(..., description="Finished games together with a result")
    wins: int = Field(..., description="Games won together")
    draws: int = Field(..., description="Games drawn together")
    win_rate: float = Field(..., description="wins / games")


class SpLeaderboardOut(CamelModel):
    """`GET /api/spades/leaderboard`: both kinds, by the APP_TZ day a game finished."""

    period: Literal["today", "week", "season", "all"] = Field(..., description="The period")
    sort: Literal["wins", "rate", "made"] = Field(..., description="The sort")
    rows: list[SpLeaderboardRowOut] = Field(..., description="Ranked rows, then unranked")
    pairs: list[SpPairOut] = Field(
        ..., description="Best partner pairs (top 20): most wins, then rate, then games"
    )
    generated_at: int = Field(..., description="Epoch ms")


class SpPartnerOut(CamelModel):
    """One of a player's partners and their record together."""

    player_id: str = Field(..., description="The partner's registry id")
    games: int = Field(..., description="Finished games together with a result")
    wins: int = Field(..., description="Games won together")
    win_rate: float = Field(..., description="wins / games")


class SpPlayerStatsOut(_Totals):
    """`GET /api/spades/players/{id}/stats`: all time, both kinds."""

    best_score: int | None = Field(..., description="Best team score in a finished game")
    partners: list[SpPartnerOut] = Field(..., description="Most games together first")


class SpHeadToHeadOut(CamelModel):
    """`GET /api/spades/players/{a}/vs/{b}`: finished games both had a result in."""

    a: PlayerSummaryOut
    b: PlayerSummaryOut
    games: int = Field(..., description="Games both played to the end")
    together: int = Field(..., description="Games as partners")
    together_wins: int = Field(..., description="Games won as partners")
    against: int = Field(..., description="Games on opposite teams")
    a_wins: int = Field(..., description="Games against where a's team won")
    b_wins: int = Field(..., description="Games against where b's team won")
    draws: int = Field(..., description="Games against that were drawn")
