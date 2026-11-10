"""The rows derived when a game ends (app: stats.ts), written to ishaara_game_players."""

from dataclasses import dataclass
from typing import Any

from app.domain.ishaara.types import State, other


@dataclass(slots=True)
class _TeamTally:
    words_found: int = 0
    wrong_picks: int = 0
    trap_hit: bool = False
    clues: int = 0
    clue_sum: int = 0
    finite_clues: int = 0
    penalties: int = 0


def _count_guess(x: _TeamTally, entry: dict[str, Any]) -> None:
    if entry.get("identity") == entry["team"]:
        x.words_found += 1
    else:
        x.wrong_picks += 1
    if entry.get("identity") == "trap":
        x.trap_hit = True


def _count_clue(x: _TeamTally, entry: dict[str, Any]) -> None:
    x.clues += 1
    n = entry.get("number")
    if isinstance(n, int) and not isinstance(n, bool):
        x.clue_sum += n
        x.finite_clues += 1


def _tally(state: State) -> dict[str, _TeamTally]:
    """Per team, from the log: undone guesses have already left it, so every guess counted stands."""
    t = {"sun": _TeamTally(), "moon": _TeamTally()}
    for e in state["log"]:
        if e["type"] == "guess":
            _count_guess(t[e["team"]], e)
        elif e["type"] == "clue":
            _count_clue(t[e["team"]], e)
        elif e["type"] == "not-allowed":
            # The ruling is logged under the team that called it; the penalty is the other team's.
            t[other(e["team"])].penalties += 1
    return t


def _row(seat: dict[str, Any], x: _TeamTally, winner: object) -> dict[str, Any]:
    giver = seat["role"] == "clue-giver"
    return {
        "playerId": seat["playerId"],
        "team": seat["team"],
        "role": seat["role"],
        "won": winner == seat["team"],
        "wordsFound": x.words_found,
        "wrongPicks": x.wrong_picks,
        "trapHit": x.trap_hit,
        "cluesGiven": x.clues if giver else 0,
        "avgClueNumber": x.clue_sum / x.finite_clues if giver and x.finite_clues > 0 else None,
        "penalties": x.penalties if giver else 0,
    }


def player_rows(state: State) -> list[dict[str, Any]]:
    """One camelCase row per seat: the team's picks for everyone; clues and penalties for the clue-giver."""
    t = _tally(state)
    return [_row(p, t[p["team"]], state["winner"]) for p in state["players"]]


def revealed_count(state: State) -> int:
    """How many cards are face up: the server's round_count."""
    return sum(1 for r in state["revealed"] if r is not None)
