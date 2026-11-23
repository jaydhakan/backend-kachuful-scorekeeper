"""Sikke, the app's coins (rewards plan §9): wallets, ledgers and what a game paid."""

import uuid

from pydantic import Field

from app.schemas.common import CamelModel


class LevelOut(CamelModel):
    """A level by lifetime Sikke; the app owns the names."""

    n: int = Field(..., description="Level number, 1-6")
    from_: int = Field(..., alias="from", description="Lifetime Sikke where this level starts")
    next: int | None = Field(..., description="Where the next level starts; null at the top")


class SikkeOut(CamelModel):
    """`GET /api/players/{id}/sikke`: a player's wallet."""

    player_id: uuid.UUID = Field(..., description="The player (the kept one, after a merge)")
    balance: int = Field(..., description="Earned minus spent (nothing is spent yet)")
    lifetime: int = Field(..., description="Every Sikke ever earned")
    level: LevelOut = Field(..., description="The level `lifetime` reaches")


class SikkeEntryOut(CamelModel):
    """One credit in a player's ledger."""

    id: uuid.UUID = Field(..., description="Entry id")
    kind: str = Field(..., description="finish, win, achievement, streak, bounty or sitara")
    amount: int = Field(..., description="Sikke credited")
    game: str | None = Field(..., description="The progression game (both Spades kinds: spades)")
    game_id: uuid.UUID | None = Field(..., description="The game that paid it, if any")
    code: str | None = Field(..., description="The achievement's code (achievement entries)")
    period: str | None = Field(..., description="The achievement's day or month, if any")
    earned_at: int = Field(..., description="Epoch ms: the game's end or the achievement's")


class GameSikkeEntryOut(CamelModel):
    """One credit a game paid a player."""

    kind: str = Field(..., description="The entry's kind")
    amount: int = Field(..., description="Sikke credited")
    code: str | None = Field(..., description="The achievement's code (achievement entries)")


class LevelUpOut(CamelModel):
    """The levels a game moved a player between."""

    from_: int = Field(..., alias="from", description="Level before this game's Sikke")
    to: int = Field(..., description="Level now")


class GameSikkePlayerOut(CamelModel):
    """What one game paid one of its players."""

    player_id: uuid.UUID = Field(..., description="Registry player id")
    amount: int = Field(..., description="Sum of `entries`")
    entries: list[GameSikkeEntryOut] = Field(..., description="Oldest first")
    level: LevelOut = Field(..., description="The player's level now")
    level_up: LevelUpOut | None = Field(
        ..., description="Set when the lifetime without this game's amount was a lower level"
    )


class GameSikkeOut(CamelModel):
    """`GET /api/games/{id}/sikke`: what a game paid each of its players, in seat order."""

    items: list[GameSikkePlayerOut] = Field(..., description="Seat order")
