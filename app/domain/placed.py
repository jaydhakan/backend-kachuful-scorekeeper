"""Kachuful's ended games as places at a table: what bounties are paid from."""

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
