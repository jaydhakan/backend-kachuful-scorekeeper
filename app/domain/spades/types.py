"""Spades' shared types and JavaScript value helpers (app: src/games/spades/domain/types.ts).

States, actions, cards, rules, hands and games are plain camelCase JSON dicts, exactly as the app writes them.
"""

from dataclasses import dataclass
from typing import Any, Literal

# The public table (SpPublic) as stored and compared to the fixtures.
State = dict[str, Any]
Card = dict[str, Any]
Hands = list[list[Card]]
Seat = int
TeamId = Literal["a", "b"]

STANDARD_RULES: dict[str, Any] = {
    "target": 200,
    "timerSec": 30,
    "nil": True,
    "blindNil": False,
    "minTeamBid": None,
    "mercy": None,
}


class _Undefined:
    """JavaScript's `undefined`: a key that is absent, as distinct from one set to null."""

    def __repr__(self) -> str:
        return "UNDEFINED"


UNDEFINED: Any = _Undefined()


@dataclass(frozen=True, slots=True)
class ApplyCtx:
    """What the server supplies with an action: its clock, the private hands and any randomness.

    None stands for the app's undefined/null; `draw` and `deal` are raw (possibly malformed) JSON.
    """

    now: int
    hands: Hands | None
    draw: Any = None
    deal: Any = None
    rev: int | None = None
    last_seen_at: int | None = None


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """`noop`: accepted with nothing changed (rev not bumped). On a refusal, state and hands are the input's."""

    ok: bool
    state: State
    hands: Hands | None
    noop: bool = False
    error: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class SeatResult:
    """The join and rules routes' outcome: the new state when ok, else the input state and the refusal."""

    ok: bool
    state: State
    error: dict[str, str] | None = None


def is_number(x: object) -> bool:
    """`typeof x === 'number'`: a bool is not a number in JavaScript."""
    return isinstance(x, int | float) and not isinstance(x, bool)


def is_integer(x: object) -> bool:
    """`Number.isInteger(x)`: 3.0 counts, True does not."""
    if isinstance(x, float):
        return x.is_integer()
    return is_number(x)


def num_eq(x: object, n: float) -> bool:
    """`x === n` for a number n: strict, so True is not 1."""
    return is_number(x) and x == n


def js_num(x: Any) -> Any:
    """A whole float as an int, as JSON.stringify would print it; anything else unchanged."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def js_truthy(x: object) -> bool:
    """JavaScript truthiness: an empty list or dict is truthy, unlike in Python."""
    if x is None or x is UNDEFINED or x is False:
        return False
    if is_number(x):
        return x != 0 and x == x  # NaN is falsy
    if isinstance(x, str):
        return x != ""
    return True


def get(x: object, key: str) -> Any:
    """`x.key` on JSON: UNDEFINED when x isn't an object or lacks the key."""
    if isinstance(x, dict):
        return x.get(key, UNDEFINED)
    return UNDEFINED


def at(x: object, i: int) -> Any:
    """`x[i]` on JSON: UNDEFINED when x isn't a list or i is out of range."""
    if isinstance(x, list) and 0 <= i < len(x):
        return x[i]
    return UNDEFINED
