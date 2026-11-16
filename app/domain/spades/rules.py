"""Spades' rule engine (app: rules.ts): the server runs every action through `apply_action`.

States are plain dicts; every function returns new dicts and lists and never mutates its input.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.ishaara.text import js_trim, utf16_len
from app.domain.spades.bidding import bid_problem, blind_allowed
from app.domain.spades.cards import TRICKS, distinct_cards, draw_winner, is_card, is_deal, same_card
from app.domain.spades.legal import play_problem
from app.domain.spades.seats import SEATS, as_seat, is_seat, next_seat
from app.domain.spades.standings import DRAW_RESULT, end_result, game_over, scores_of, totals
from app.domain.spades.timer import (
    AWAY_AFTER,
    DEAL_ANYONE_MS,
    deadline_for,
    timed_turn,
    timeout_bid,
    timeout_play,
)
from app.domain.spades.trick import trick_winner
from app.domain.spades.types import (
    UNDEFINED,
    ApplyCtx,
    ApplyResult,
    Card,
    Hands,
    Seat,
    SeatResult,
    State,
    get,
    js_num,
    js_truthy,
)
from app.domain.spades.validate import rules_ok

NAME_MAX = 40
# The six reactions a seat may send; anything else is refused.
REACTIONS = ("\U0001f44f", "\U0001f62c", "\U0001f525", "\U0001f602", "\U0001f64f", "\U0001fae3")
_NUMERIC_RULES = ("target", "timerSec", "minTeamBid", "mercy")


@dataclass(frozen=True, slots=True)
class _Rejected:
    error: dict[str, str]


@dataclass(frozen=True, slots=True)
class _W:
    """A step's outcome: the new public state and the private hands."""

    s: State
    hands: Hands | None


class _Noop:
    """Accepted, nothing changed."""


_NOOP = _Noop()
_Step = _W | _Rejected | _Noop
_Handler = Callable[[_W, dict[str, Any], Seat, ApplyCtx], _Step]
_NOT_YOURS = _Rejected({"code": "not_your_turn"})


def _bad(reason: str) -> _Rejected:
    return _Rejected({"code": "invalid_action", "reason": reason})


def _four(v: Any) -> list[Any]:
    return [v, v, v, v]


def _empty_seat() -> dict[str, Any]:
    return {"who": None, "ready": False, "away": False, "timeouts": 0}


def _rules_copy(r: dict[str, Any]) -> dict[str, Any]:
    """`{...rules}`, with whole floats as ints so the stored JSON reads as the app's would."""
    return {**r, **{k: js_num(r[k]) for k in _NUMERIC_RULES if k in r}}


def is_over(s: State) -> bool:
    """Finished or cancelled."""
    return s["status"] in ("finished", "cancelled")


def seat_of(s: State, user_id: str) -> Seat | None:
    """The seat an account sits in, or None."""
    for i, x in enumerate(s["seats"]):
        if get(x.get("who"), "userId") == user_id:
            return i
    return None


def tricks_won(s: State) -> list[int]:
    """Tricks each seat has taken this hand."""
    won = _four(0)
    for t in s["tricks"]:
        won[t["winner"]] += 1
    return won


def last_trick(s: State) -> dict[str, Any] | None:
    """The last finished trick: this hand's, or the previous hand's between hands."""
    if s["tricks"]:
        trick: dict[str, Any] = s["tricks"][-1]
        return trick
    prev = s["history"][-1].get("tricks") if s["history"] else None
    return prev[-1] if prev else None


def totals_of(s: State) -> dict[str, dict[str, Any]]:
    """The table's totals from its finished hands."""
    return totals(s["history"])


def new_table(
    *, id: str, code: str, name: str, rules: dict[str, Any], host: dict[str, Any], now: int
) -> State:
    """A table as created: the host in seat 0, the rest empty."""
    seats = [_empty_seat() for _ in SEATS]
    seats[0] = {**seats[0], "who": host}
    return {
        "kind": "spades-online",
        "schemaVersion": 1,
        "id": id,
        "code": code,
        "name": name,
        "rev": 1,
        "createdAt": now,
        "updatedAt": now,
        "status": "lobby",
        "paused": None,
        "host": 0,
        "rules": _rules_copy(rules),
        "seats": seats,
        "drawn": None,
        "dealer": None,
        "phase": "draw",
        "bids": _four(None),
        "blind": _four(False),
        "looked": _four(False),
        "trick": [],
        "tricks": [],
        "broken": False,
        "turn": None,
        "turnStartedAt": None,
        "deadline": None,
        "history": [],
        "result": None,
        "next": None,
    }


def _stamp(s: State, prev_rev: int, now: int) -> State:
    """A changed state, stamped: rev, time and the timer's deadline."""
    return {**s, "rev": prev_rev + 1, "updatedAt": now, "deadline": deadline_for(s)}


def _rotate_partners(s: State) -> list[dict[str, Any]]:
    """Hosts keep their seat; the other three move one place round, so the host's partner changes."""
    h: int = s["host"]
    old = s["seats"]
    seats = list(old)
    seats[(h + 2) % 4] = old[(h + 1) % 4]
    seats[(h + 3) % 4] = old[(h + 2) % 4]
    seats[(h + 1) % 4] = old[(h + 3) % 4]
    return seats


def _first_free(seats: list[dict[str, Any]]) -> int:
    return next((i for i, x in enumerate(seats) if not js_truthy(x["who"])), -1)


def _sit_lobby(
    state: State, who: dict[str, Any], seat: Seat | None, at: Seat | None, now: int
) -> SeatResult:
    seats = list(state["seats"])
    if at is not None:
        if seat is None or seat == at:
            return SeatResult(ok=True, state=state)
        if js_truthy(seats[seat]["who"]):
            return SeatResult(ok=False, state=state, error=_bad("seat-taken").error)
        seats[seat] = seats[at]
        seats[at] = _empty_seat()
        host = seat if state["host"] == at else state["host"]
        moved = {**state, "seats": seats, "host": host}
        return SeatResult(ok=True, state=_stamp(moved, state["rev"], now))
    free = _first_free(seats)
    if free < 0:
        return SeatResult(ok=False, state=state, error=_bad("full").error)
    to = seat if seat is not None else free
    if js_truthy(seats[to]["who"]):
        return SeatResult(ok=False, state=state, error=_bad("seat-taken").error)
    seats[to] = {**_empty_seat(), "who": who}
    return SeatResult(ok=True, state=_stamp({**state, "seats": seats}, state["rev"], now))


def _sit_replace(
    state: State, who: dict[str, Any], seat: Seat | None, at: Seat | None, now: int
) -> SeatResult:
    if state["paused"] != "replace":
        return SeatResult(ok=False, state=state, error=_bad("started").error)
    if at is not None:
        return SeatResult(ok=False, state=state, error=_bad("already").error)
    seats = list(state["seats"])
    free = _first_free(seats)
    if seat is not None and seat != free:
        return SeatResult(ok=False, state=state, error=_bad("seat-taken").error)
    seats[free] = {"who": who, "ready": True, "away": False, "timeouts": 0}
    started = state["turnStartedAt"] if state["turn"] is None else now
    nxt = {**state, "seats": seats, "paused": None, "turnStartedAt": started}
    return SeatResult(ok=True, state=_stamp(nxt, state["rev"], now))


def sit(state: State, who: dict[str, Any], seat: Any, now: int) -> SeatResult:
    """Join or move in the lobby (seat None: the lowest free), or take the open seat after a Replace."""
    if seat is not None and not is_seat(seat):
        return SeatResult(ok=False, state=state, error=_bad("bad-seat").error)
    if is_over(state):
        return SeatResult(ok=False, state=state, error=_bad("finished").error)
    at = seat_of(state, get(who, "userId"))
    if state["status"] == "lobby":
        return _sit_lobby(state, who, as_seat(seat), at, now)
    return _sit_replace(state, who, as_seat(seat), at, now)


def set_rules(state: State, actor: Any, rules: Any, name: Any, now: int) -> SeatResult:
    """The rules route: host only, lobby only; name None keeps the table's."""
    if actor is None or as_seat(actor) != state["host"]:
        return SeatResult(ok=False, state=state, error=_NOT_YOURS.error)
    if is_over(state):
        return SeatResult(ok=False, state=state, error=_bad("finished").error)
    if state["status"] != "lobby":
        return SeatResult(ok=False, state=state, error=_bad("started").error)
    if not rules_ok(rules) or (name is not None and not isinstance(name, str)):
        return SeatResult(ok=False, state=state, error=_bad("bad-rules").error)
    n: str = state["name"] if name is None else js_trim(name)
    if not n or utf16_len(n) > NAME_MAX:
        return SeatResult(ok=False, state=state, error=_bad("bad-rules").error)
    nxt = {**state, "rules": _rules_copy(rules), "name": n}
    return SeatResult(ok=True, state=_stamp(nxt, state["rev"], now))


def rematch_table(old: State, *, id: str, code: str, now: int, swap: bool = False) -> State:
    """The rematch's lobby: the same people, rules and name, nobody ready yet."""
    host_who = old["seats"][old["host"]]["who"]
    fresh = new_table(
        id=id, code=code, name=old["name"], rules=old["rules"], host=host_who, now=now
    )
    seats = [{**_empty_seat(), "who": x["who"]} for x in old["seats"]]
    base = {**fresh, "host": old["host"], "seats": seats}
    return {**base, "seats": _rotate_partners(base)} if js_truthy(swap) else base


def _finish(s: State, result: dict[str, str] | None, now: int) -> State:
    return {
        **s,
        "status": "finished" if result else "cancelled",
        "result": result,
        "finishedAt": now,
        "paused": None,
        "turn": None,
        "turnStartedAt": None,
    }


def _player_id(x: dict[str, Any]) -> Any:
    who = x.get("who")
    pid = who.get("playerId") if isinstance(who, dict) else None
    return pid


def _end_hand(s: State, now: int) -> State:
    """The 13th trick is in: the hand goes to history (public now), then the game ends or the deal passes."""
    won = tricks_won(s)
    hand = {
        "index": len(s["history"]),
        "dealer": s["dealer"],
        "bids": list(s["bids"]),
        "blind": list(s["blind"]),
        "teamTricks": [won[0] + won[2], won[1] + won[3]],
        "nilTricks": [won[i] if b == 0 else None for i, b in enumerate(s["bids"])],
        "tricks": s["tricks"],
        "players": [_player_id(x) for x in s["seats"]],
        "phase": "done",
        "doneAt": now,
    }
    history = [*s["history"], hand]
    over = game_over(scores_of(totals(history)), s["rules"])
    if isinstance(over, dict):
        return _finish({**s, "history": history}, over, now)
    dealer = next_seat(s["dealer"])
    return {
        **s,
        "history": history,
        "phase": "handEnd",
        "dealer": dealer,
        "turn": dealer,
        "turnStartedAt": now,
    }


def _place_bid(s: State, seat: Seat, bid: Any, blind: bool, now: int) -> State:
    """Records a bid, fills in any Blind Nil whose turn it now is, and starts play after the fourth."""
    bids, looked, blinds = list(s["bids"]), list(s["looked"]), list(s["blind"])
    bids[seat] = bid
    looked[seat] = True
    if blind:
        blinds[seat] = True
    nxt = next_seat(seat)
    while bids[nxt] is None and blinds[nxt]:
        bids[nxt] = 0
        looked[nxt] = True
        nxt = next_seat(nxt)
    base = {**s, "bids": bids, "looked": looked, "blind": blinds, "turnStartedAt": now}
    if any(b is None for b in bids):
        return {**base, "turn": nxt}
    return {**base, "phase": "playing", "turn": next_seat(s["dealer"]), "trick": []}


def _need_hands(w: _W) -> Hands:
    if w.hands is None:
        raise ValueError("spades: the hands are missing for a table in play")
    return w.hands


def _play_card(w: _W, seat: Seat, card: Card, now: int) -> _W:
    s = w.s
    hands = [
        [c for c in h if not same_card(c, card)] if i == seat else h
        for i, h in enumerate(_need_hands(w))
    ]
    trick = [*s["trick"], {"seat": seat, "card": card}]
    broken = s["broken"] or card["suit"] == "spades"
    if len(trick) < 4:
        nxt = {**s, "trick": trick, "broken": broken, "turn": next_seat(seat), "turnStartedAt": now}
        return _W(nxt, hands)
    winner = trick_winner(trick)
    done = {
        **s,
        "trick": [],
        "tricks": [*s["tricks"], {"plays": trick, "winner": winner}],
        "broken": broken,
        "turn": winner,
        "turnStartedAt": now,
    }
    return _W(_end_hand(done, now) if len(done["tricks"]) == TRICKS else done, hands)


def _timeout(w: _W, ctx: ApplyCtx) -> _Step:
    s = w.s
    if not timed_turn(s):
        return _NOOP
    deadline = deadline_for(s, ctx.last_seen_at)
    if deadline is None or ctx.now < deadline:
        # The sender saw an older turn, which has since been settled by someone else.
        return _NOOP if ctx.rev is not None and ctx.rev < s["rev"] else _bad("too-early")
    seat: Seat = s["turn"]
    if s["phase"] == "bidding":
        bid = timeout_bid(seat, s["bids"], s["rules"])
        out = _W(_place_bid(s, seat, bid, False, ctx.now), w.hands)
    else:
        card = timeout_play(_need_hands(w)[seat], s["trick"], s["broken"])
        out = _play_card(w, seat, card, ctx.now)
    seats = list(out.s["seats"])
    timeouts = seats[seat]["timeouts"] + 1
    seats[seat] = {**seats[seat], "timeouts": timeouts, "away": timeouts >= AWAY_AFTER}
    return _W({**out.s, "seats": seats}, out.hands)


def _set_ready(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    ready = a["type"] == "ready"
    if w.s["seats"][seat]["ready"] == ready:
        return _NOOP
    seats = list(w.s["seats"])
    seats[seat] = {**seats[seat], "ready": ready}
    return _W({**w.s, "seats": seats}, w.hands)


def _shuffle(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _W({**w.s, "seats": _rotate_partners(w.s)}, w.hands)


def _draw_for_deal(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if not all(js_truthy(x["who"]) and x["ready"] for x in s["seats"]):
        return _bad("not-ready")
    if not distinct_cards(ctx.draw, 4):
        return _bad("bad-draw")
    dealer = draw_winner(ctx.draw)
    nxt = {
        **s,
        "status": "playing",
        "phase": "deal",
        "drawn": list(ctx.draw),
        "dealer": dealer,
        "turn": dealer,
        "turnStartedAt": ctx.now,
    }
    return _W(nxt, w.hands)


def _cancel(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _W(_finish(w.s, None, ctx.now), w.hands)


def _end_game(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _W(_finish(w.s, end_result(scores_of(totals_of(w.s))), ctx.now), w.hands)


def _draw_game(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _W(_finish(w.s, dict(DRAW_RESULT), ctx.now), w.hands)


def _not_started(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _bad("not-started")


def _not_finished(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _bad("not-finished")


def _started(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    return _bad("started")


def _back(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    # "I'm back": the only thing Ready means once the game has started.
    x = w.s["seats"][seat]
    return w if x["away"] or x["timeouts"] else _NOOP


def _deal(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if s["phase"] not in ("deal", "handEnd"):
        return _bad("wrong-phase")
    started = s["turnStartedAt"] if s["turnStartedAt"] is not None else 0
    if seat != s["dealer"] and ctx.now < started + DEAL_ANYONE_MS:
        return _NOT_YOURS
    if not is_deal(ctx.deal):
        return _bad("bad-deal")
    nxt = {
        **s,
        "phase": "bidding",
        "bids": _four(None),
        "blind": _four(False),
        "looked": _four(not blind_allowed(s["rules"])),
        "trick": [],
        "tricks": [],
        "broken": False,
        "turn": next_seat(s["dealer"]),
        "turnStartedAt": ctx.now,
    }
    return _W(nxt, [list(h) for h in ctx.deal])


def _look(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if s["looked"][seat]:
        return _NOOP
    if s["phase"] != "bidding":
        return _bad("wrong-phase")
    if s["blind"][seat]:
        return _bad("blind-locked")
    looked = list(s["looked"])
    looked[seat] = True
    return _W({**s, "looked": looked}, w.hands)


def _blind_nil(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if s["phase"] != "bidding":
        return _bad("wrong-phase")
    if not blind_allowed(s["rules"]):
        return _bad("blind-off")
    if s["bids"][seat] is not None:
        return _bad("already-bid")
    if s["looked"][seat]:
        return _bad("looked")
    if s["blind"][seat]:
        return _NOOP
    if s["turn"] == seat:
        return _W(_place_bid(s, seat, 0, True, ctx.now), w.hands)
    blind = list(s["blind"])
    blind[seat] = True
    return _W({**s, "blind": blind}, w.hands)


def _bid(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if s["phase"] != "bidding":
        return _bad("wrong-phase")
    if s["turn"] != seat:
        return _NOT_YOURS
    if not s["looked"][seat]:
        return _bad("look-first")
    bid = a.get("bid", UNDEFINED)
    why = bid_problem(bid, seat, s["bids"], s["rules"])
    if why:
        return _bad(why)
    return _W(_place_bid(s, seat, js_num(bid), False, ctx.now), w.hands)


def _play(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if s["phase"] != "playing":
        return _bad("wrong-phase")
    if s["turn"] != seat:
        return _NOT_YOURS
    raw: Any = a.get("card")
    if not is_card(raw):
        return _bad("bad-card")
    card = {"rank": raw["rank"], "suit": raw["suit"]}
    why = play_problem(card, _need_hands(w)[seat], s["trick"], s["broken"])
    if why:
        return _bad(why)
    return _play_card(w, seat, card, ctx.now)


def _replace(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    s = w.s
    if seat != s["host"]:
        return _NOT_YOURS
    target = as_seat(a.get("seat"))
    if target is None:
        return _bad("bad-seat")
    if target == s["host"]:
        return _bad("host-seat")
    if not s["seats"][target]["away"]:
        return _bad("not-away")
    if s["trick"]:
        return _bad("mid-trick")
    seats = list(s["seats"])
    seats[target] = _empty_seat()
    return _W({**s, "seats": seats, "paused": "replace"}, w.hands)


_LOBBY_ANYONE: dict[str, _Handler] = {"ready": _set_ready, "unready": _set_ready}
_LOBBY_HOST: dict[str, _Handler] = {
    "shufflePartners": _shuffle,
    "drawForDeal": _draw_for_deal,
    "cancelGame": _cancel,
    "endGame": _not_started,
    "drawGame": _not_started,
    "replace": _not_started,
    "rematch": _not_finished,
}
_PLAY_HOST: dict[str, _Handler] = {
    "endGame": _end_game,
    "drawGame": _draw_game,
    "cancelGame": _cancel,
    "rematch": _not_finished,
}
_PLAY: dict[str, _Handler] = {
    "ready": _back,
    "unready": _started,
    "shufflePartners": _started,
    "drawForDeal": _started,
    "deal": _deal,
    "look": _look,
    "blindNil": _blind_nil,
    "bid": _bid,
    "play": _play,
    "replace": _replace,
}


def _type(a: dict[str, Any]) -> str | None:
    t = a.get("type")
    return t if isinstance(t, str) else None


def _lobby_step(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    t = _type(a) or ""
    if t in _LOBBY_ANYONE:
        return _LOBBY_ANYONE[t](w, a, seat, ctx)
    if t in _LOBBY_HOST:
        return _LOBBY_HOST[t](w, a, seat, ctx) if seat == w.s["host"] else _NOT_YOURS
    return _bad("not-started")


def _play_step(w: _W, a: dict[str, Any], seat: Seat, ctx: ApplyCtx) -> _Step:
    t = _type(a) or ""
    if t in _PLAY_HOST:
        return _PLAY_HOST[t](w, a, seat, ctx) if seat == w.s["host"] else _NOT_YOURS
    if w.s["paused"]:
        return _bad("paused")
    handler = _PLAY.get(t)
    return handler(w, a, seat, ctx) if handler else _bad("wrong-phase")


def _over_step(w: _W, a: dict[str, Any], seat: Seat | None) -> _Step:
    t = _type(a)
    if t == "timeout":
        return _NOOP
    if t != "rematch":
        return _bad("finished")
    if seat is None or seat != w.s["host"]:
        return _NOT_YOURS
    if w.s["next"] is not None:
        return _bad("already")
    nid = a.get("id")
    if not isinstance(nid, str) or not nid:
        return _bad("wrong-phase")
    return _W({**w.s, "next": nid}, w.hands)


def _seated(state: State, actor: Any) -> Seat | None:
    seat = as_seat(actor)
    return seat if seat is not None and js_truthy(state["seats"][seat]["who"]) else None


def _route(w: _W, a: dict[str, Any], seat: Seat | None, ctx: ApplyCtx) -> _Step:
    t = _type(a)
    if t == "react":
        if seat is None:
            return _NOT_YOURS
        emoji = a.get("emoji")
        return _NOOP if isinstance(emoji, str) and emoji in REACTIONS else _bad("bad-emoji")
    if is_over(w.s):
        return _over_step(w, a, seat)
    if seat is None:
        return _NOT_YOURS
    if t == "timeout":
        return _timeout(w, ctx)
    if w.s["status"] == "lobby":
        return _lobby_step(w, a, seat, ctx)
    return _play_step(w, a, seat, ctx)


def _user_id(x: dict[str, Any]) -> Any:
    return get(x.get("who"), "userId")


def _back_again(s: State, before: State, seat: Seat | None, t: str | None) -> State:
    """Any move of the person's own, other than a timeout their phone sent, means they are back."""
    if seat is None or t == "timeout":
        return s
    if _user_id(s["seats"][seat]) != _user_id(before["seats"][seat]):
        return s
    x = s["seats"][seat]
    if not (x["away"] or x["timeouts"]):
        return s
    seats = list(s["seats"])
    seats[seat] = {**x, "away": False, "timeouts": 0}
    return {**s, "seats": seats}


def apply_action(state: State, action: Any, actor: Any, ctx: ApplyCtx) -> ApplyResult:
    """The game's one rule engine: on a refusal the state and hands come back unchanged."""
    a: dict[str, Any] = action if isinstance(action, dict) else {}
    seat = _seated(state, actor)
    out = _route(_W(state, ctx.hands), a, seat, ctx)
    if isinstance(out, _Noop):
        return ApplyResult(ok=True, state=state, hands=ctx.hands, noop=True)
    if isinstance(out, _Rejected):
        return ApplyResult(ok=False, state=state, hands=ctx.hands, error=out.error)
    s = _back_again(out.s, state, seat, _type(a))
    return ApplyResult(ok=True, state=_stamp(s, state["rev"], ctx.now), hands=out.hands)
