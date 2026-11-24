"""Ended games as places at a table: what bounties are paid from.

Kachuful and Kaali Teeri store each seat's place. A team game (Spades, Ishaara) places a side:
the winners 1, the rest 2, so a draw puts nobody above anybody.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlacedSeat:
    player_id: str
    place: int
    seat: int


@dataclass(frozen=True)
class PlacedGame:
    """One finished game: every seat's place (a team game gives a side the same place)."""

    game_id: str
    ended_at: int
    seats: tuple[PlacedSeat, ...]
    teams: bool = False


def placed_games(rows: list[tuple[str, int, str, int, int]]) -> list[PlacedGame]:
    """Finished games from seat rows (game, ended ms, player, place, seat), seats in seat order."""
    games: dict[str, tuple[int, list[PlacedSeat]]] = {}
    for game_id, ended_at, player_id, place, seat in rows:
        games.setdefault(game_id, (ended_at, []))[1].append(PlacedSeat(player_id, place, seat))
    return [
        PlacedGame(game_id, ended_at, tuple(sorted(seats, key=lambda s: s.seat)))
        for game_id, (ended_at, seats) in games.items()
    ]


WINNER_PLACE = 1
OTHERS_PLACE = 2


def place_of(place: int | None, won: bool) -> int:
    """A seat's stored place, else its side's: 1 for a win, 2 for a loss or a draw."""
    if place is not None:
        return place
    return WINNER_PLACE if won else OTHERS_PLACE


def placed_rows(
    rows: list[tuple[str, int, str, int | None, bool, int]],
) -> list[tuple[str, int, str, int, int]]:
    """Result rows (game, ended ms, player, stored place, won, seat) as placed seat rows."""
    return [(g, at, p, place_of(place, won), seat) for g, at, p, place, won, seat in rows]


def placed_results(
    rows: list[tuple[str, int, str, int | None, bool, int]],
) -> list[PlacedGame]:
    """Finished games from result rows (game, ended ms, player, stored place, won, seat)."""
    return placed_games(placed_rows(rows))
