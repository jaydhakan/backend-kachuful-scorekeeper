"""Upload checks for a table-mode game (app: validate.ts): the first thing wrong with it, or None.

Uploads are arbitrary JSON: where the app's code would throw on a malformed shape, this returns the
reason of the check it would have thrown in.
"""

from typing import Any

from app.domain.ishaara.text import js_trim, utf16_len
from app.domain.spades.bidding import bid_order, bid_problem, blind_allowed
from app.domain.spades.standings import end_result, game_over, scores_of, totals
from app.domain.spades.table import dealer_for, tricks_ok
from app.domain.spades.types import (
    UNDEFINED,
    get,
    is_integer,
    is_number,
    js_truthy,
    num_eq,
)

TARGET_MIN = 50
TARGET_MAX = 1000
PLAYER_NAME_MAX = 14
_PHASES = ("bidding", "tricks", "done")
_FIVE_MIN = 5 * 60_000
_TEN_MIN = 10 * 60_000


def _bad(reason: str, hand_index: int | None = None) -> dict[str, Any]:
    return {"reason": reason, "handIndex": hand_index}


def _target_ok(t: object) -> bool:
    if t is None:
        return True
    if not isinstance(t, int | float) or not is_integer(t):
        return False
    return TARGET_MIN <= t <= TARGET_MAX and t % 10 == 0


def rules_ok(r: Any) -> bool:
    """Rules a game may be played under: the choices Edit rules offers and nothing else."""
    if not isinstance(r, dict):
        return False
    if not _target_ok(r.get("target", UNDEFINED)):
        return False
    timer = r.get("timerSec", UNDEFINED)
    if timer is not None and not (num_eq(timer, 30) or num_eq(timer, 60)):
        return False
    nil, blind = r.get("nil"), r.get("blindNil")
    if not isinstance(nil, bool) or not isinstance(blind, bool) or (blind and not nil):
        return False
    least, mercy = r.get("minTeamBid", UNDEFINED), r.get("mercy", UNDEFINED)
    return (least is None or num_eq(least, 4)) and (mercy is None or num_eq(mercy, -200))


def _set_size(values: list[Any]) -> int:
    """`new Set(values).size`: primitives by value (1 and 1.0 alike, True apart), objects by identity."""
    keys: set[tuple[Any, ...]] = set()
    for v in values:
        if isinstance(v, bool):
            keys.add(("b", v))
        elif is_number(v) or isinstance(v, str):
            keys.add(("p", v))
        elif v is None or v is UNDEFINED:
            keys.add(("n", v is None))
        else:
            keys.add(("o", id(v)))
    return len(keys)


def _bad_name(p: object) -> bool:
    name = get(p, "name")
    return not isinstance(name, str) or not js_trim(name) or utf16_len(name) > PLAYER_NAME_MAX


def _players_ok(players: Any) -> bool:
    if not isinstance(players, list):
        return False
    ids = [get(p, "id") for p in players]
    pids = [get(p, "playerId") for p in players if js_truthy(get(p, "playerId"))]
    if len(ids) != 4 or _set_size(ids) != 4 or _set_size(pids) != len(pids):
        return False
    return not any(_bad_name(p) for p in players)


def _dealer_ok(g: dict[str, Any]) -> bool:
    first = g.get("firstDealerIndex")
    if not isinstance(first, int | float) or not is_integer(first) or not 0 <= first <= 3:
        return False
    return g.get("direction") in ("cw", "ccw")


def _bids_problem(h: dict[str, Any], r: dict[str, Any]) -> bool:
    """Bids made in turn order and legal under the rules."""
    bids = h["bids"]
    so: list[Any] = [None, None, None, None]
    gap = False
    for s in bid_order(int(h["dealer"])):
        b = bids[s]
        if b is None:
            gap = True
            continue
        # A bid after an empty turn means bids were entered out of order.
        if gap or bid_problem(b, s, so, r):
            return True
        so[s] = b
    return h["phase"] != "bidding" and gap


def _blind_problem(h: dict[str, Any], r: dict[str, Any]) -> bool:
    return any(
        not isinstance(x, bool) or (x and (not num_eq(h["bids"][s], 0) or not blind_allowed(r)))
        for s, x in enumerate(h["blind"])
    )


def _tricks_problem(h: dict[str, Any]) -> bool:
    nil = h.get("nilTricks")
    if not isinstance(nil, list) or len(nil) != 4:
        return True
    team = h.get("teamTricks", UNDEFINED)
    no_nil = all(n is None for n in nil)
    if h["phase"] == "bidding":
        return not (team is None and no_nil)
    if h["phase"] == "tricks" and team is None:
        return not no_nil
    return not (js_truthy(team) and tricks_ok(h, team, nil))


def _hand_problem(h: dict[str, Any], r: dict[str, Any]) -> str | None:
    """Bids in turn order and legal; tricks present as far as the phase says."""
    for key in ("bids", "blind"):
        if not isinstance(h.get(key), list) or len(h[key]) != 4:
            return "bids"
    if _bids_problem(h, r) or _blind_problem(h, r):
        return "bids"
    return "tricks" if _tricks_problem(h) else None


def _hand_invalid(
    g: dict[str, Any], hands: list[Any], i: int, manual: bool
) -> dict[str, Any] | None:
    h = hands[i]
    if not num_eq(get(h, "dealer"), dealer_for(g, i)):
        return _bad("dealer", i)
    last = len(hands) - 1
    phase = h.get("phase")
    still_open = phase != "done"
    # Ending by hand drops the hand in progress, so it may stay unfinished.
    if (
        not num_eq(h.get("index"), i)
        or phase not in _PHASES
        or (still_open and i < last)
        or (still_open and g.get("status") == "finished" and not manual)
    ):
        return _bad("hands", i)
    # No hand after the one that decided the game.
    if i < last and phase == "done":
        over = game_over(scores_of(totals(hands[: i + 1])), g["rules"])
        if over is not None and over != "tie":
            return _bad("hands", i + 1)
    why = _hand_problem(h, g["rules"])
    return _bad(why, i) if why else None


def _same_result(a: Any, b: dict[str, str] | str | None) -> bool:
    if not js_truthy(a) or not isinstance(b, dict):
        return False
    return bool(get(a, "winner") == b["winner"] and get(a, "endedBy") == b["endedBy"])


def _result_ok(g: dict[str, Any], hands: list[Any]) -> bool:
    scores = scores_of(totals(hands))
    final = game_over(scores, g["rules"])
    decided = isinstance(final, dict)
    r = g.get("result", UNDEFINED)
    if g.get("status") != "finished":
        return r is UNDEFINED
    if not js_truthy(r):
        return False
    ended = get(r, "endedBy")
    if ended in ("target", "mercy"):
        return _same_result(r, final)
    if ended == "end":
        return _same_result(r, end_result(scores)) and not decided
    return ended == "draw" and get(r, "winner") == "draw" and not decided


def _times_invalid(g: dict[str, Any], hands: list[Any], now: int) -> dict[str, Any] | None:
    created, updated = g.get("createdAt", UNDEFINED), g.get("updatedAt", UNDEFINED)
    stamps = [created, updated, g.get("finishedAt", UNDEFINED), *(get(h, "doneAt") for h in hands)]
    if any(not is_number(t) or t > now + _TEN_MIN for t in stamps if t is not UNDEFINED):
        return _bad("times")
    has_created, has_updated = created is not UNDEFINED, updated is not UNDEFINED
    if has_created and has_updated and updated < created:
        return _bad("times")
    for h in hands:
        done = get(h, "doneAt")
        early = has_created and done is not UNDEFINED and done < created
        late = has_updated and done is not UNDEFINED and done > updated + _FIVE_MIN
        if early or late:
            return _bad("times", h["index"])
    return None


def validate(game: Any, now: int) -> dict[str, Any] | None:
    """Whether a table-mode game would be accepted (`None`), or `{reason, handIndex}`; `now` is the server's."""
    g: dict[str, Any] = game if isinstance(game, dict) else {}
    if not _players_ok(g.get("players")):
        return _bad("players")
    if not rules_ok(g.get("rules")):
        return _bad("rules")
    if not _dealer_ok(g):
        return _bad("dealer")
    if g.get("status") not in ("active", "finished", "cancelled"):
        return _bad("result")
    hands = g["hands"] if isinstance(g.get("hands"), list) else []
    manual = get(g.get("result"), "endedBy") in ("end", "draw")
    for i in range(len(hands)):
        bad = _hand_invalid(g, hands, i, manual)
        if bad:
            return bad
    if not _result_ok(g, hands):
        return _bad("result")
    return _times_invalid(g, hands, now)
