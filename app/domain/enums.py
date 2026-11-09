"""Enums shared across the domain."""

from enum import StrEnum


class GameKind(StrEnum):
    """Which game a stored row is; `games.kind` is checked against these values."""

    KACHUFUL = "kachuful"
    KAALI_TEERI = "kaali-teeri"
    ISHAARA = "ishaara"
