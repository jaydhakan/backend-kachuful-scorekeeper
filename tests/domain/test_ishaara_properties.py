"""Property tests: random action sequences over random keys keep Ishaara's invariants."""

from typing import Any

from hypothesis import event, given, settings
from hypothesis import strategies as st

from app.domain.ishaara import TABLE, Actor, apply_action, words_left
from tests.domain.test_ishaara_rules import Kit, giver, plain_key, started

_NUMBERS: list[int | str] = [0, 1, 2, 3, 9, "inf"]
_KINDS = [
    "clue",
    "clue",
    "guess",
    "penaltyOwn",
    "guess",
    "guess",
    "guess",
    "endTurn",
    "timeout",
    "undoLast",
    "skip",
    "notAllowed",
    "penaltyReveal",
    "anyGuess",
]


@st.composite
def kits(draw: st.DrawFn) -> Kit:
    """An active game over a shuffled key, with or without a timer."""
    starting = draw(st.sampled_from(["sun", "moon"]))
    key = draw(st.permutations(plain_key(starting)))
    timer = draw(st.sampled_from([None, 60]))
    return started(starting=starting, key=list(key), rules={"timerSec": timer})


@st.composite
def moves(draw: st.DrawFn) -> tuple[str, int, str, int | str, int]:
    """One move: kind, card (25 is off the board), a team, a clue number and the clock step."""
    # Resigning ends a game at once, so it is rare: most games should play out.
    kind = "resign" if draw(st.integers(0, 1999)) == 1000 else draw(st.sampled_from(_KINDS))
    return (
        kind,
        draw(st.integers(0, 25)),
        draw(st.sampled_from(["sun", "moon"])),
        draw(st.sampled_from(_NUMBERS)),
        draw(st.integers(0, 20_000)),
    )


def _to_action(
    state: dict[str, Any], key: list[str], move: tuple[str, int, str, int | str, int]
) -> tuple[Actor, dict[str, Any]]:
    kind, card, team, number, _ = move
    if kind == "clue":
        return giver(state["turn"]), {"type": "clue", "word": "zqx", "number": number}
    if kind == "guess":
        # Steer clear of the trap here so games also end by a team finding every word.
        down = [i for i, r in enumerate(state["revealed"]) if r is None and key[i] != "trap"]
        return TABLE, {"type": "guess", "card": down[card % len(down)]}
    if kind == "anyGuess":
        return (TABLE if team == "sun" else giver(team)), {"type": "guess", "card": card}
    if kind == "penaltyOwn" and state["pending"]:
        picker = "moon" if state["pending"]["penaltyFor"] == "sun" else "sun"
        own = [i for i, r in enumerate(state["revealed"]) if r is None and key[i] == picker]
        return giver(picker), {"type": "penaltyReveal", "card": own[card % len(own)]}
    if kind in ("notAllowed", "penaltyReveal", "penaltyOwn"):
        return giver(team), {
            "type": "penaltyReveal" if kind == "penaltyOwn" else kind,
            "card": card,
        }
    if kind == "resign":
        return TABLE, {"type": "resign", "team": team}
    return TABLE, {"type": kind}


def _leak(state: dict[str, Any], key: list[str]) -> str | None:
    """Where the public state names a face-down card's identity, if anywhere."""
    if state["status"] != "finished" and state["solution"] is not None:
        return "solution before the end"
    if state["undo"] is not None and "identity" in state["undo"]:
        return "undo holds an identity"
    for i in range(25):
        if state["revealed"][i] is not None and state["revealed"][i] != key[i]:
            return f"card {i} shows the wrong identity"
        if state["revealed"][i] is None and any(
            e.get("card") == i and "identity" in e for e in state["log"]
        ):
            return f"log names face-down card {i}"
    return None


def _check(before: Kit, action: dict[str, Any], ok: bool, after: dict[str, Any]) -> None:
    assert _leak(after, before.key) is None
    for i in range(25):
        if before.state["revealed"][i] is not None and after["revealed"][i] is None:
            assert action["type"] == "undoLast"
    left = after["guessesLeft"]
    assert left == "inf" or left >= 0
    trap = "trap" in after["revealed"]
    all_found = words_left(after, "sun") == 0 or words_left(after, "moon") == 0
    resigned = ok and action["type"] == "resign"
    assert (after["status"] == "finished") == (trap or all_found or resigned)


@settings(max_examples=300, deadline=None)
@given(kits(), st.lists(moves(), min_size=150, max_size=300))
def test_random_games_keep_every_invariant(
    k: Kit, plan: list[tuple[str, int, str, int | str, int]]
) -> None:
    for move in plan:
        if k.state["status"] == "finished":
            break
        actor, action = _to_action(k.state, k.key, move)
        now = k.now + move[4]
        r = apply_action(k.state, k.key, action, actor, now)
        _check(k, action, r.ok, r.state)
        k = Kit(state=r.state, key=k.key, now=now)
    event(
        f"{k.state['status']} {k.state['endedBy']} up={sum(r is not None for r in k.state['revealed'])}"
    )
