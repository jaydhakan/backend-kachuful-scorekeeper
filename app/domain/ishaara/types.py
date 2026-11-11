"""Ishaara's shared types (app: src/games/ishaara/domain/types.ts). The public state is plain camelCase JSON."""

from dataclasses import dataclass
from typing import Any, Literal

Team = Literal["sun", "moon"]
Identity = Literal["sun", "moon", "blank", "trap"]
ActorKind = Literal["table", "clue-giver"]

# The public state as the app writes it (IshaaraPublic): stored as JSON and compared to the fixtures.
State = dict[str, Any]

GRID = 25
BLANKS = 7
TEAMS: tuple[Team, Team] = ("sun", "moon")


@dataclass(frozen=True, slots=True)
class Actor:
    """Who sends an action: the table (the host's screen) or a team's clue-giver."""

    kind: ActorKind
    team: Team | None = None


TABLE = Actor(kind="table")


@dataclass(frozen=True, slots=True)
class ActionResult:
    """`state` is the new state when ok, else the settled one (an overdue timer still ends the turn)."""

    ok: bool
    state: State
    error: dict[str, str] | None = None


def as_team(value: object) -> Team:
    """A team name, typed; ValueError for anything else."""
    if value == "sun":
        return "sun"
    if value == "moon":
        return "moon"
    raise ValueError(f"not a team: {value!r}")


def other(team: str) -> Team:
    """The opposing team."""
    return "moon" if team == "sun" else "sun"
