"""Scoring one hand (app: scoring.ts): contracts, Nil, bags and the 10-bag penalty."""

from typing import Any

from app.domain.spades.seats import seats_of
from app.domain.spades.types import UNDEFINED, Seat, at, get, is_number, js_truthy, num_eq

NIL_POINTS = 100
BLIND_NIL_POINTS = 200
BAG_LIMIT = 10
BAG_PENALTY = 100


def is_nil(h: dict[str, Any], s: Seat) -> bool:
    """Whether the seat bid Nil."""
    return num_eq(at(get(h, "bids"), s), 0)


def _team_tricks(h: dict[str, Any]) -> Any:
    tt = get(h, "teamTricks")
    return [0, 0] if tt is None or tt is UNDEFINED else tt


def _well_typed(h: dict[str, Any]) -> bool:
    """Shapes the app's scorer would otherwise crash on or turn into NaN: never scored here."""
    tt = _team_tricks(h)
    if not isinstance(tt, list) or not all(is_number(at(tt, i)) for i in (0, 1)):
        return False
    return all(is_number(b) for b in h["bids"])


def scorable(h: Any) -> bool:
    """Whether the hand has everything the scorer reads."""
    if not isinstance(h, dict) or h.get("teamTricks", UNDEFINED) is None:
        return False
    bids, nil_tricks = h.get("bids"), h.get("nilTricks")
    if not isinstance(bids, list) or any(b is None for b in bids):
        return False
    if any(num_eq(b, 0) and not is_number(at(nil_tricks, s)) for s, b in enumerate(bids)):
        return False
    return _well_typed(h)


def _nil_score(h: dict[str, Any], s: Seat) -> dict[str, Any]:
    tricks = at(get(h, "nilTricks"), s)
    tricks = 0 if tricks is None or tricks is UNDEFINED else tricks
    blind = js_truthy(at(get(h, "blind"), s))
    value = BLIND_NIL_POINTS if blind else NIL_POINTS
    made = tricks == 0
    return {
        "seat": s,
        "blind": blind,
        "made": made,
        "tricks": tricks,
        "points": value if made else -value,
    }


def score_team(h: dict[str, Any], team: str, bags_before: Any = 0) -> dict[str, Any]:
    """One team's score for a hand (the app's TeamScore), given the bags it carried in."""
    team_tricks = _team_tricks(h)[0 if team == "a" else 1]
    nils: list[dict[str, Any]] = []
    contract = nil_took = bidders = 0
    for s in seats_of(team):
        if is_nil(h, s):
            nil = _nil_score(h, s)
            nils.append(nil)
            nil_took += nil["tricks"]
        else:
            bidders += 1
            bid = at(get(h, "bids"), s)
            contract += 0 if bid is None or bid is UNDEFINED else bid
    took = team_tricks - nil_took
    # A Nil bidder's tricks are always bags and never count toward the partner's contract.
    points = sum(x["points"] for x in nils) + nil_took
    bags = nil_took
    made: bool | None = None
    if bidders:
        made = took >= contract
        if made:
            points += 10 * contract + (took - contract)
            bags += took - contract
        else:
            points -= 10 * contract
    bags_after = bags_before + bags
    penalty = bags_after // BAG_LIMIT - bags_before // BAG_LIMIT
    return {
        "team": team,
        "contract": contract,
        "took": took,
        "made": made,
        "points": points,
        "bags": bags,
        "nils": nils,
        "bagsBefore": bags_before,
        "bagsAfter": bags_after,
        "penalty": penalty,
        "net": points - BAG_PENALTY * penalty,
    }


def score_hand(h: dict[str, Any], bags_before: dict[str, Any] | None = None) -> dict[str, Any]:
    """Both teams' scores for a hand: `{"a": TeamScore, "b": TeamScore}`."""
    before = bags_before if bags_before is not None else {"a": 0, "b": 0}
    return {"a": score_team(h, "a", before["a"]), "b": score_team(h, "b", before["b"])}
