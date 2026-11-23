"""The Kursi against the app's golden fixtures."""

from typing import Any

from app.domain.kursi import kursi_from
from app.domain.placed import PlacedGame, PlacedSeat
from tests.domain.golden import camel, load


def _game(raw: dict[str, Any]) -> PlacedGame:
    seats = tuple(PlacedSeat(s["playerId"], s["place"], s["seat"]) for s in raw["seats"])
    return PlacedGame(raw["gameId"], raw["endedAt"], seats, raw.get("teams", False))


def test_kursi_matches_the_app() -> None:
    fx = load("kursi-cases")
    kursi, reigns = kursi_from([_game(g) for g in fx["games"]])
    assert camel(kursi) == fx["expect"]["kursi"]
    assert camel(reigns) == fx["expect"]["reigns"]
