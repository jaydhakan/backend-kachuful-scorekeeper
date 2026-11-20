"""The Kursi against the app's golden fixtures."""

from typing import Any

from app.domain.kursi import KursiGame, KursiSeat, kursi_from
from tests.domain.golden import camel, load


def _game(raw: dict[str, Any]) -> KursiGame:
    seats = tuple(KursiSeat(s["playerId"], s["place"], s["seat"]) for s in raw["seats"])
    return KursiGame(raw["gameId"], raw["endedAt"], seats, raw.get("teams", False))


def test_kursi_matches_the_app() -> None:
    fx = load("kursi-cases")
    kursi, reigns = kursi_from([_game(g) for g in fx["games"]])
    assert camel(kursi) == fx["expect"]["kursi"]
    assert camel(reigns) == fx["expect"]["reigns"]
