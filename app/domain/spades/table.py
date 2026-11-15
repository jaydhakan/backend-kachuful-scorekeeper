"""Table mode (app: table.ts): hands entered by a scorer at a real table, and their totals."""

from collections.abc import Sequence
from typing import Any

from app.domain.spades.cards import TRICKS
from app.domain.spades.scoring import is_nil
from app.domain.spades.seats import TEAMS, seats_of
from app.domain.spades.standings import game_over, scores_of, totals
from app.domain.spades.types import UNDEFINED, Seat, at, get, is_integer, js_truthy, num_eq


def dealer_for(g: dict[str, Any], index: int) -> Seat:
    """The dealer moves one seat clockwise each hand; `direction` never changes it."""
    return int(g["firstDealerIndex"] + index) % 4


def new_hand(g: dict[str, Any]) -> dict[str, Any]:
    """The next hand to bid."""
    index = len(g["hands"])
    return {
        "index": index,
        "dealer": dealer_for(g, index),
        "bids": [None, None, None, None],
        "blind": [False, False, False, False],
        "teamTricks": None,
        "nilTricks": [None, None, None, None],
        "phase": "bidding",
    }


def _trick_count(n: object) -> bool:
    return isinstance(n, int | float) and is_integer(n) and 0 <= n <= TRICKS


def _team_fits(h: dict[str, Any], ti: int, team_tricks: list[Any], nil_tricks: list[Any]) -> bool:
    seats = seats_of(TEAMS[ti])
    nils = [s for s in seats if is_nil(h, s)]
    if any(not is_nil(h, s) and nil_tricks[s] is not None for s in seats):
        return False
    if not all(_trick_count(nil_tricks[s]) for s in nils):
        return False
    total = sum(nil_tricks[s] for s in nils)
    return bool(total == team_tricks[ti] if len(nils) == 2 else total <= team_tricks[ti])


def tricks_ok(h: dict[str, Any], team_tricks: Any, nil_tricks: Any) -> bool:
    """Team a's and the Nil bidders' tricks fit the bids: 0 to 13 each, Nil tricks within the team's."""
    if not isinstance(team_tricks, list) or len(team_tricks) != 2:
        return False
    if not all(_trick_count(n) for n in team_tricks):
        return False
    if team_tricks[0] + team_tricks[1] != TRICKS or not isinstance(nil_tricks, list):
        return False
    if len(nil_tricks) != 4:
        return False
    return all(_team_fits(h, ti, team_tricks, nil_tricks) for ti in range(2))


def nil_range(
    h: dict[str, Any], seat: Seat, team_a: int, nil_tricks: Sequence[Any]
) -> dict[str, int]:
    """The tricks a Nil bidder can have taken, given team a's tricks and the other Nil partner's."""
    team = team_a if seat % 2 == 0 else TRICKS - team_a
    mate = (seat + 2) % 4
    if not is_nil(h, mate):
        return {"min": 0, "max": team}
    other = nil_tricks[mate]
    # Both Nil: they took all the team's tricks between them.
    if other is None:
        return {"min": 0, "max": team}
    return {"min": team - other, "max": team - other}


def table_hand(e: dict[str, Any]) -> dict[str, Any]:
    """A finished table-mode hand from what the scorer entered: bids, team a's tricks and each Nil's."""
    bids = e["bids"]
    nil_in, blind_in = e.get("nilTricks"), e.get("blind")
    nil = []
    for s, b in enumerate(bids):
        n = at(nil_in, s)
        nil.append((0 if n is None or n is UNDEFINED else n) if num_eq(b, 0) else None)
    hand: dict[str, Any] = {
        "index": e["index"],
        "dealer": e["dealer"],
        "bids": list(bids),
        "blind": [num_eq(b, 0) and js_truthy(at(blind_in, s)) for s, b in enumerate(bids)],
        "teamTricks": [e["teamA"], TRICKS - e["teamA"]],
        "nilTricks": nil,
        "phase": "done",
    }
    if get(e, "doneAt") is not UNDEFINED:
        hand["doneAt"] = e["doneAt"]
    return hand


def game_totals(g: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The game's totals from its hands."""
    return totals(g["hands"])


def game_result(g: dict[str, Any]) -> dict[str, str] | str | None:
    """Whether the finished hands end the game: a result, 'tie' (play another hand) or None."""
    return game_over(scores_of(game_totals(g)), g["rules"])
