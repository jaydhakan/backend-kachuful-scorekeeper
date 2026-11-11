"""Ishaara models: create, actions, the public game, the key, summaries, boards and stats."""

import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel
from app.schemas.players import PlayerSummaryOut

Team = Literal["sun", "moon"]
Role = Literal["clue-giver", "guesser"]
Identity = Literal["sun", "moon", "blank", "trap"]
ActionType = Literal[
    "clue",
    "guess",
    "endTurn",
    "timeout",
    "undoLast",
    "resign",
    "skip",
    "notAllowed",
    "penaltyReveal",
]


class SeatedIn(CamelModel):
    """A seat at create: the app's Seated (a registry player with a team and role)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=40, description="The app's seat id")
    name: str = Field(..., min_length=1, max_length=40, description="Display name")
    emoji: str = Field(..., max_length=16, description="Avatar emoji")
    color: str = Field(..., max_length=16, description="Avatar colour (hex)")
    player_id: str | None = Field(None, max_length=40, description="Registry player id")
    team: str = Field(..., max_length=8, description="`sun` or `moon` (the domain checks it)")
    role: str = Field(..., max_length=16, description="`clue-giver` or `guesser`")


class RulesIn(CamelModel):
    """IshaaraRules; the domain's validateNew checks the values."""

    model_config = ConfigDict(extra="forbid")

    timer_sec: int | None = Field(..., description="60, 120, 180 or null (no timer)")
    clue_entry: str = Field(..., max_length=16, description="`typed` or `spoken`")
    packs: list[str] = Field(..., max_length=10, description="Word packs the words came from")
    best_of: int = Field(..., description="1 or 3")


class CreateIshaaraIn(CamelModel):
    """`POST /api/ishaara/games`: seats, 25 words and rules; the server deals the key."""

    id: uuid.UUID = Field(..., description="The new game's id, chosen by the app")
    players: list[SeatedIn] = Field(
        ..., max_length=40, description="Every seat, each with a playerId"
    )
    words: list[str] = Field(..., max_length=25, description="The 25 board words, in grid order")
    rules: RulesIn = Field(..., description="Timer, clue entry, packs and match length")


class ClueGiversIn(CamelModel):
    """Who gives clues in the rematch, per team (defaults rotate)."""

    sun: uuid.UUID | None = Field(None, description="A Sun seat's playerId")
    moon: uuid.UUID | None = Field(None, description="A Moon seat's playerId")


class RematchIn(CamelModel):
    """`POST /api/ishaara/games/{id}/rematch`."""

    id: uuid.UUID = Field(..., description="The new game's id")
    words: list[str] = Field(..., max_length=25, description="25 fresh board words")
    clue_givers: ClueGiversIn | None = Field(None, description="Clue-givers per team")


class ActionIn(CamelModel):
    """`POST /api/ishaara/games/{id}/actions`: the domain's Action plus `actionId` and `rev`.

    Values stay loose here: the rules engine answers a bad card or clue with `invalid_action`.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: uuid.UUID = Field(..., description="Client id: a retry with it never acts twice")
    rev: int = Field(..., ge=0, description="The rev the device last saw")
    type: ActionType = Field(..., description="Which action")
    word: str | None = Field(None, max_length=64, description="`clue`: the word (null if spoken)")
    number: int | Literal["inf"] | None = Field(None, description="`clue`: 0-9 or 'inf'")
    card: int | None = Field(None, description="`guess`, `penaltyReveal`: card index 0-24")
    team: str | None = Field(None, max_length=8, description="`resign`: the team resigning")

    def action(self) -> dict[str, Any]:
        """The domain's Action: the type and only the fields the client sent."""
        fields = self.model_dump(by_alias=True, exclude_unset=True, exclude={"action_id", "rev"})
        return {**fields, "type": self.type}


class MeOut(CamelModel):
    """The caller's place in the game."""

    host: bool = Field(..., description="The caller created the game (its device is the table)")
    team: Team | None = Field(..., description="The team the caller gives clues for, else null")


class IshaaraGameOut(CamelModel):
    """A game as the table and clue phones see it: public state only, never the key."""

    game: dict[str, Any] = Field(..., description="IshaaraPublic, exactly as the domain writes it")
    me: MeOut = Field(..., description="The caller's role")
    server_now: int = Field(..., description="Server clock, epoch ms (for timers)")


class IshaaraKeyOut(CamelModel):
    """`GET /api/ishaara/games/{id}/key`: for the two clue-giver accounts only."""

    key: list[Identity] = Field(..., description="The 25 identities, in grid order")
    team: Team = Field(..., description="The caller's team")


class SummaryPlayerOut(CamelModel):
    """A seat in a summary."""

    player_id: str = Field(..., description="Registry player id")
    name: str = Field(..., description="Display name at the table")
    emoji: str = Field(..., description="Avatar emoji")
    color: str = Field(..., description="Avatar colour")
    team: Team = Field(..., description="Team")
    role: Role = Field(..., description="Role")


class HostOut(CamelModel):
    """Who hosts the game."""

    name: str = Field(..., description="The host's display name")


class MatchOut(CamelModel):
    """The match a game belongs to."""

    game: int = Field(..., description="Game number in the match, from 1")
    sun_wins: int = Field(..., description="Sun's wins so far")
    moon_wins: int = Field(..., description="Moon's wins so far")


class WordsLeftOut(CamelModel):
    """Face-down words per team."""

    sun: int = Field(..., description="Sun words not yet found")
    moon: int = Field(..., description="Moon words not yet found")


class IshaaraSummaryOut(CamelModel):
    """A list item, from `games.summary`: never the key or face-down identities."""

    id: str = Field(..., description="Game id")
    created_at: int = Field(..., description="Epoch ms")
    updated_at: int = Field(..., description="Epoch ms of the last change")
    finished_at: int | None = Field(..., description="Epoch ms, once finished")
    status: Literal["waiting", "active", "finished"] = Field(..., description="Game status")
    turn: Team = Field(..., description="Whose turn")
    starting: Team = Field(..., description="Who started (9 words)")
    winner: Team | None = Field(..., description="Winner, once finished (null if abandoned)")
    ended_by: Literal["all-found", "trap", "resign", "abandoned"] | None = Field(
        ..., description="How it ended"
    )
    match: MatchOut = Field(..., description="Match score")
    words_left: WordsLeftOut = Field(..., description="Words left per team")
    revealed: int = Field(..., description="Cards face up")
    host: HostOut = Field(..., description="The host")
    players: list[SummaryPlayerOut] = Field(..., description="Every seat")
    my_team: Team | None = Field(..., description="The team the caller gives clues for")


class IshaaraPageOut(CamelModel):
    """`GET /api/ishaara/games`."""

    items: list[IshaaraSummaryOut] = Field(..., description="This page, newest change first")
    next_cursor: str | None = Field(..., description="Pass as ?cursor=; null at the end")


class IshaaraLeaderboardRowOut(CamelModel):
    """One ranked player."""

    rank: int | None = Field(..., description="Rank; null below MIN_GAMES for `rate`")
    player: PlayerSummaryOut = Field(..., description="The player")
    value: float = Field(..., description="The sorted value")
    games: int = Field(..., description="Finished games")
    wins: int = Field(..., description="Games won")
    win_rate: float = Field(..., description="wins / games")
    words_found: int = Field(..., description="Own words the team found")
    wrong_picks: int = Field(..., description="Other cards the team picked")
    trap_hits: int = Field(..., description="Games lost on the trap")
    clue_games: int = Field(..., description="Games as clue-giver")
    clue_wins: int = Field(..., description="Wins as clue-giver")
    clues_given: int = Field(..., description="Clues given")
    avg_clue_number: float | None = Field(..., description="Mean finite clue number")
    penalties: int = Field(..., description="Clues ruled out")


class IshaaraLeaderboardOut(CamelModel):
    """`GET /api/ishaara/leaderboard`."""

    period: Literal["today", "week", "season", "all"] = Field(..., description="Period")
    sort: Literal["wins", "rate", "found"] = Field(..., description="Sort")
    rows: list[IshaaraLeaderboardRowOut] = Field(..., description="Ranked rows")
    generated_at: int = Field(..., description="Epoch ms")


class IshaaraPlayerStatsOut(CamelModel):
    """`GET /api/ishaara/players/{id}/stats`: the domain's IshaaraTally minus playerId."""

    games: int = Field(..., description="Finished games")
    wins: int = Field(..., description="Games won")
    win_rate: float = Field(..., description="wins / games (0 with no games)")
    words_found: int = Field(..., description="Own words the team found")
    wrong_picks: int = Field(..., description="Other cards the team picked")
    trap_hits: int = Field(..., description="Games lost on the trap")
    clue_games: int = Field(..., description="Games as clue-giver")
    clue_wins: int = Field(..., description="Wins as clue-giver")
    clues_given: int = Field(..., description="Clues given")
    avg_clue_number: float | None = Field(..., description="Mean finite clue number")
    penalties: int = Field(..., description="Clues ruled out")


class IshaaraHeadToHeadOut(CamelModel):
    """`GET /api/ishaara/players/{a}/vs/{b}`: the domain's headToHead."""

    a: PlayerSummaryOut = Field(..., description="Player a")
    b: PlayerSummaryOut = Field(..., description="Player b")
    games: int = Field(..., description="Finished games both sat in")
    together: int = Field(..., description="Games on the same team")
    together_wins: int = Field(..., description="Of those, won")
    against: int = Field(..., description="Games on opposite teams")
    a_wins: int = Field(..., description="Of those, a won")
    b_wins: int = Field(..., description="Of those, b won")
