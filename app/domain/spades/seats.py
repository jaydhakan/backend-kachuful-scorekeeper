"""Seats and teams (app: seats.ts): seats 0 and 2 are team a, 1 and 3 team b; play goes 0 → 1 → 2 → 3."""

from app.domain.spades.types import Seat, TeamId, is_number

SEATS: tuple[Seat, Seat, Seat, Seat] = (0, 1, 2, 3)
TEAMS: tuple[TeamId, TeamId] = ("a", "b")


def is_seat(x: object) -> bool:
    """Only 0, 1, 2 or 3 (1.0 is 1 in JavaScript; True and "1" are not seats)."""
    return is_number(x) and x in SEATS


def as_seat(x: object) -> Seat | None:
    """The seat as an int, or None when x isn't a seat."""
    if isinstance(x, int | float) and is_seat(x):
        return int(x)
    return None


def team_of(s: Seat) -> TeamId:
    """The seat's team."""
    return "a" if s % 2 == 0 else "b"


def partner_of(s: Seat) -> Seat:
    """The seat across the table."""
    return (s + 2) % 4


def seats_of(t: str) -> tuple[Seat, Seat]:
    """A team's two seats."""
    return (0, 2) if t == "a" else (1, 3)


def next_seat(s: Seat) -> Seat:
    """Clockwise: the next seat to bid, play or deal."""
    return (s + 1) % 4


def other_team(t: str) -> TeamId:
    """The opposing team."""
    return "b" if t == "a" else "a"
