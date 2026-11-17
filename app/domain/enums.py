"""Enums shared across the domain."""

from enum import StrEnum


class GameKind(StrEnum):
    """Which game a stored row is; `games.kind` is checked against these values."""

    KACHUFUL = "kachuful"
    KAALI_TEERI = "kaali-teeri"
    ISHAARA = "ishaara"
    SPADES = "spades"
    SPADES_ONLINE = "spades-online"


class ProgressionGame(StrEnum):
    """The game an achievement, award, feed item or closed period belongs to.

    Both Spades kinds are the game `spades`: one catalogue, one board.
    """

    KACHUFUL = "kachuful"
    SPADES = "spades"
    KAALI_TEERI = "kaali-teeri"
    ISHAARA = "ishaara"


GAME_OF_KIND: dict[GameKind, ProgressionGame] = {
    GameKind.KACHUFUL: ProgressionGame.KACHUFUL,
    GameKind.KAALI_TEERI: ProgressionGame.KAALI_TEERI,
    GameKind.ISHAARA: ProgressionGame.ISHAARA,
    GameKind.SPADES: ProgressionGame.SPADES,
    GameKind.SPADES_ONLINE: ProgressionGame.SPADES,
}


def kinds_of(game: ProgressionGame) -> tuple[GameKind, ...]:
    """The stored kinds that make up a game."""
    return tuple(k for k, g in GAME_OF_KIND.items() if g == game)
