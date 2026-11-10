"""Ishaara's rule engine (app: rules.ts): the server runs every action through `apply_action`.

States are plain dicts; every function returns a new dict and never mutates its input.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.ishaara.clue import as_int, clue_allowed, clue_number, guesses_for
from app.domain.ishaara.text import js_trim, nfc
from app.domain.ishaara.types import (
    GRID,
    TEAMS,
    ActionResult,
    Actor,
    State,
    Team,
    as_team,
    other,
)

# A mis-tapped reveal can be undone from the table for this long, if nothing else happened since.
UNDO_MS = 10_000
# After this long the host can skip a penalty pick nobody made.
SKIP_AFTER_MS = 60_000
MIN_TEAM = 2


@dataclass(frozen=True, slots=True)
class _Rejected:
    error: dict[str, str]


_Step = State | _Rejected
_NOT_YOURS = _Rejected({"code": "not_your_turn"})


def _bad(reason: str) -> _Rejected:
    return _Rejected({"code": "invalid_action", "reason": reason})


def words_of(state: State, team: str) -> int:
    """The starting team has 9 words, the other 8."""
    return 9 if team == state["starting"] else 8


def words_left(state: State, team: str) -> int:
    """Words of a team still face down, from public state alone."""
    return words_of(state, team) - sum(1 for r in state["revealed"] if r == team)


def clue_giver_of(state: State, team: str) -> dict[str, Any] | None:
    """The seat giving clues for a team."""
    for p in state["players"]:
        if p.get("team") == team and p.get("role") == "clue-giver":
            seat: dict[str, Any] = p
            return seat
    return None


def new_game(
    *,
    game_id: str,
    players: list[dict[str, Any]],
    words: list[str],
    rules: dict[str, Any],
    starting: str,
    now: int,
    match: dict[str, Any] | None = None,
) -> State:
    """A game as created on the server: waiting for both clue-givers to open their key."""
    return {
        "kind": "ishaara",
        "schemaVersion": 1,
        "id": game_id,
        "rev": 1,
        "createdAt": now,
        "updatedAt": now,
        "players": [dict(p) for p in players],
        "words": list(words),
        "revealed": [None] * GRID,
        "starting": starting,
        "turn": starting,
        "clue": None,
        "guessesLeft": 0,
        "guessedThisTurn": 0,
        "deadline": None,
        "pending": None,
        "undo": None,
        "joined": {"sun": False, "moon": False},
        "log": [],
        "rules": dict(rules),
        "match": dict(match) if match is not None else {"game": 1, "sunWins": 0, "moonWins": 0},
        "status": "waiting",
        "winner": None,
        "endedBy": None,
        "solution": None,
        "next": None,
    }


def join(state: State, team: str, now: int) -> State:
    """A clue-giver opened their key; the game starts once both have."""
    if state["joined"][team] or state["status"] == "finished":
        return state
    joined = {**state["joined"], team: True}
    status = "active" if all(joined[t] for t in TEAMS) else state["status"]
    return {**state, "joined": joined, "status": status, "rev": state["rev"] + 1, "updatedAt": now}


def _end_turn(s: State) -> State:
    return {
        **s,
        "turn": other(s["turn"]),
        "clue": None,
        "guessesLeft": 0,
        "guessedThisTurn": 0,
        "deadline": None,
    }


def _finish(s: State, winner: Team | None, ended_by: str, key: list[str], now: int) -> State:
    match = dict(s["match"])
    if winner == "sun":
        match["sunWins"] += 1
    if winner == "moon":
        match["moonWins"] += 1
    return {
        **s,
        "status": "finished",
        "winner": winner,
        "endedBy": ended_by,
        "finishedAt": now,
        "clue": None,
        "guessesLeft": 0,
        "deadline": None,
        "pending": None,
        "undo": None,
        "match": match,
        "solution": list(key),
    }


def _after_reveal(s: State, by: str, identity: str, key: list[str], now: int) -> State | None:
    """The trap loses for whoever revealed it; a team with every word found wins, on any turn."""
    if identity == "trap":
        return _finish(s, other(by), "trap", key, now)
    if identity in TEAMS and words_left(s, identity) == 0:
        return _finish(s, as_team(identity), "all-found", key, now)
    return None


def settle(state: State, now: int) -> State:
    """Applies an overdue timer before anything else: the next action after the deadline ends the turn."""
    deadline = state["deadline"]
    if state["status"] != "active" or deadline is None or now < deadline:
        return state
    s = _end_turn({**state, "undo": None})
    entry = {"at": now, "team": state["turn"], "type": "timeout"}
    return {**s, "log": [*s["log"], entry], "rev": state["rev"] + 1, "updatedAt": now}


def _typed_word(s: State, raw: object) -> str | _Rejected:
    if not isinstance(raw, str):
        return _bad("clue-format")
    check = clue_allowed(raw, s)
    if not check["ok"]:
        return _bad("clue-format" if check["reason"] in ("empty", "format") else "clue-blocked")
    return js_trim(nfc(raw))


def _clue_word(s: State, action: dict[str, Any]) -> str | _Rejected | None:
    if s["rules"]["clueEntry"] == "typed":
        return _typed_word(s, action.get("word"))
    # Spoken: the clue was said aloud, so only the number is sent.
    return None if action.get("word") is None else _bad("clue-format")


def _clue(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "clue-giver" or actor.team != s["turn"]:
        return _NOT_YOURS
    if s["pending"]:
        return _bad("penalty-pending")
    if s["clue"]:
        return _bad("clue-given")
    number = clue_number(action.get("number"))
    if number is None:
        return _bad("clue-format")
    word = _clue_word(s, action)
    if isinstance(word, _Rejected):
        return word
    timer = s["rules"]["timerSec"]
    entry: dict[str, Any] = {"at": now, "team": s["turn"], "type": "clue", "number": number}
    if word is not None:
        entry["word"] = word
    return {
        **s,
        "clue": {"team": s["turn"], "word": word, "number": number},
        "guessesLeft": guesses_for(number),
        "guessedThisTurn": 0,
        "deadline": now + timer * 1000 if timer else None,
        "log": [*s["log"], entry],
    }


def _card(raw: object) -> int | None:
    card = as_int(raw)
    return card if card is not None and 0 <= card < GRID else None


def _guess_blocked(s: State, actor: Actor) -> _Rejected | None:
    if actor.kind != "table":
        return _NOT_YOURS
    if s["pending"]:
        return _bad("penalty-pending")
    if not s["clue"]:
        return _bad("no-clue")
    left = s["guessesLeft"]
    if left != "inf" and left <= 0:
        return _bad("no-guesses-left")
    return None


def _undo_slot(s: State, card: int, now: int) -> dict[str, Any]:
    return {
        "card": card,
        "until": now + UNDO_MS,
        "turn": s["turn"],
        "clue": s["clue"],
        "guessesLeft": s["guessesLeft"],
        "guessedThisTurn": s["guessedThisTurn"],
        "deadline": s["deadline"],
    }


def _guess(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    blocked = _guess_blocked(s, actor)
    if blocked:
        return blocked
    card = _card(action.get("card"))
    if card is None:
        return _bad("bad-card")
    if s["revealed"][card] is not None:
        return _bad("revealed")
    identity, team = key[card], s["turn"]
    revealed = list(s["revealed"])
    revealed[card] = identity
    left = s["guessesLeft"]
    entry = {"at": now, "team": team, "type": "guess", "card": card}
    entry |= {"word": s["words"][card], "identity": identity}
    nxt: State = {
        **s,
        "revealed": revealed,
        "guessedThisTurn": s["guessedThisTurn"] + 1,
        "guessesLeft": "inf" if left == "inf" else left - 1,
        "undo": _undo_slot(s, card, now),
        "log": [*s["log"], entry],
    }
    over = _after_reveal(nxt, team, identity, key, now)
    if over is not None:
        return over
    if identity != team or nxt["guessesLeft"] == 0:
        return _end_turn(nxt)
    return nxt


def _end_turn_action(
    s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int
) -> _Step:
    if actor.kind != "table":
        return _NOT_YOURS
    if s["pending"]:
        return _bad("penalty-pending")
    if not s["clue"]:
        return _bad("no-clue")
    if s["guessedThisTurn"] < 1:
        return _bad("no-guess")
    entry = {"at": now, "team": s["turn"], "type": "end"}
    return {**_end_turn(s), "log": [*s["log"], entry]}


def _without_last_guess(log: list[dict[str, Any]], card: int) -> list[dict[str, Any]]:
    """The guess leaves the log with its identity, so the card is face down in every sense again."""
    out = list(log)
    for i in range(len(out) - 1, -1, -1):
        if out[i]["type"] == "guess" and out[i].get("card") == card:
            del out[i]
            break
    return out


def _undo_last(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "table":
        return _NOT_YOURS
    u = s["undo"]
    if not u:
        return _bad("nothing-to-undo")
    if now > u["until"]:
        return _bad("too-late")
    card = u["card"]
    revealed = list(s["revealed"])
    revealed[card] = None
    log = _without_last_guess(s["log"], card)
    log.append(
        {"at": now, "team": u["turn"], "type": "undo", "card": card, "word": s["words"][card]}
    )
    return {
        **s,
        "revealed": revealed,
        "turn": u["turn"],
        "clue": u["clue"],
        "guessesLeft": u["guessesLeft"],
        "guessedThisTurn": u["guessedThisTurn"],
        "deadline": u["deadline"],
        "undo": None,
        "log": log,
    }


def _not_allowed(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "clue-giver" or actor.team == s["turn"]:
        return _NOT_YOURS
    if s["pending"]:
        return _bad("penalty-pending")
    if not s["clue"]:
        return _bad("no-clue")
    if s["guessedThisTurn"] > 0:
        return _bad("too-late")
    entry = {"at": now, "team": actor.team, "type": "not-allowed"}
    return {
        **_end_turn(s),
        "pending": {"penaltyFor": s["turn"], "since": now},
        "log": [*s["log"], entry],
    }


def _penalty_card(s: State, actor: Actor, raw: object, key: list[str]) -> int | _Rejected:
    picker = other(s["pending"]["penaltyFor"])
    if actor.kind != "clue-giver" or actor.team != picker:
        return _NOT_YOURS
    card = _card(raw)
    if card is None:
        return _bad("bad-card")
    if s["revealed"][card] is not None:
        return _bad("revealed")
    if key[card] != picker:
        return _bad("not-yours")
    return card


def _penalty_reveal(
    s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int
) -> _Step:
    if not s["pending"]:
        return _bad("no-penalty")
    card = _penalty_card(s, actor, action.get("card"), key)
    if isinstance(card, _Rejected):
        return card
    picker = other(s["pending"]["penaltyFor"])
    revealed = list(s["revealed"])
    revealed[card] = picker
    entry = {"at": now, "team": picker, "type": "penalty", "card": card}
    entry |= {"word": s["words"][card], "identity": picker}
    nxt: State = {**s, "revealed": revealed, "pending": None, "log": [*s["log"], entry]}
    return _after_reveal(nxt, picker, picker, key, now) or nxt


def _skip(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "table":
        return _NOT_YOURS
    pending = s["pending"]
    if not pending:
        return _bad("no-penalty")
    if now < pending["since"] + SKIP_AFTER_MS:
        return _bad("too-early")
    entry = {"at": now, "team": other(pending["penaltyFor"]), "type": "skip"}
    return {**s, "pending": None, "log": [*s["log"], entry]}


def _timeout(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "table":
        return _NOT_YOURS
    # settle() has already ended an overdue turn; reaching here means the deadline hasn't passed.
    return _bad("too-early")


def _resign(s: State, action: dict[str, Any], actor: Actor, key: list[str], now: int) -> _Step:
    if actor.kind != "table":
        return _NOT_YOURS
    team = action.get("team")
    if team not in TEAMS:
        return _bad("bad-team")
    return _finish(s, other(str(team)), "resign", key, now)


_Handler = Callable[[State, dict[str, Any], Actor, list[str], int], _Step]
_HANDLERS: dict[str, _Handler] = {
    "clue": _clue,
    "guess": _guess,
    "endTurn": _end_turn_action,
    "timeout": _timeout,
    "undoLast": _undo_last,
    "resign": _resign,
    "skip": _skip,
    "notAllowed": _not_allowed,
    "penaltyReveal": _penalty_reveal,
}


def _rejected(error: dict[str, str], state: State) -> ActionResult:
    return ActionResult(ok=False, state=state, error=error)


def apply_action(
    state: State, key: list[str], action: dict[str, Any], actor: Actor, now: int
) -> ActionResult:
    """Runs one action. On an error `state` is the settled state, otherwise the new one with rev bumped."""
    if state["status"] == "finished":
        return _rejected(_bad("finished").error, state)
    if state["status"] == "waiting":
        return _rejected(_bad("not-started").error, state)
    settled = settle(state, now)
    kind = action.get("type")
    # Not in the app (its Action type is closed): an unknown type is rejected, after the timer settles.
    handler = _HANDLERS.get(kind) if isinstance(kind, str) else None
    if handler is None:
        return _rejected(_bad("bad-action").error, settled)
    if kind == "timeout" and settled is not state:
        return ActionResult(ok=True, state=settled)
    # Anything after a reveal closes its undo window, except the undo itself.
    base = settled if kind == "undoLast" else {**settled, "undo": None}
    out = handler(base, action, actor, key, now)
    if isinstance(out, _Rejected):
        return _rejected(out.error, settled)
    return ActionResult(ok=True, state={**out, "rev": settled["rev"] + 1, "updatedAt": now})
