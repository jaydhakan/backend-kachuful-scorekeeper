"""Ishaara state ⇄ the `games` row: columns, summary, access, outputs and errors.

Shared by the games service, the rematch and the auto-end job; no I/O.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from app.db.models import Game, IshaaraRole, User
from app.domain.ishaara.rules import words_left
from app.domain.ishaara.stats import revealed_count
from app.domain.ishaara.types import TEAMS, State
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.ishaara import CLUE_GIVER
from app.schemas.ishaara import IshaaraGameOut, MeOut
from app.services.games import payload_hash

_REVEALS = ("guess", "penalty")


@dataclass(frozen=True)
class Access:
    """What an account is in one game."""

    host: bool
    team: str | None
    member: bool


def access_of(row: Game, roles: list[IshaaraRole], user: User) -> Access:
    """Host, clue-giver team (by the account linked at creation) and membership."""
    host = row.owner_id == user.id
    team = next((r.team for r in roles if r.role == CLUE_GIVER and r.user_id == user.id), None)
    seated = user.player_id is not None and any(r.player_id == user.player_id for r in roles)
    return Access(host=host, team=team, member=host or team is not None or seated)


def summary_of(state: State) -> dict[str, Any]:
    """The stored `games.summary`: public fields only, never the key or face-down identities."""
    return {
        "id": state["id"],
        "createdAt": state["createdAt"],
        "updatedAt": state["updatedAt"],
        "finishedAt": state.get("finishedAt"),
        "status": state["status"],
        "turn": state["turn"],
        "starting": state["starting"],
        "winner": state["winner"],
        "endedBy": state["endedBy"],
        "match": state["match"],
        "wordsLeft": {t: words_left(state, t) for t in TEAMS},
        "revealed": revealed_count(state),
        "players": [
            {
                "playerId": p["playerId"],
                "name": p["name"],
                "emoji": p["emoji"],
                "color": p["color"],
                "team": p["team"],
                "role": p["role"],
            }
            for p in state["players"]
        ],
    }


def apply_state(row: Game, state: State) -> None:
    """Write a state into its row: payload, rev, status, summary and the columns jobs filter on."""
    finished = state.get("finishedAt")
    reveals = [e["at"] for e in state["log"] if e["type"] in _REVEALS]
    row.rev = state["rev"]
    row.payload = state
    row.payload_hash = payload_hash(state)
    row.schema_version = state["schemaVersion"]
    row.status = "finished" if state["status"] == "finished" else "active"
    row.player_count = len(state["players"])
    row.round_count = revealed_count(state)
    row.summary = summary_of(state)
    row.created_at = from_ms(state["createdAt"])
    row.client_updated_at = from_ms(state["updatedAt"])
    row.finished_at = None if finished is None else from_ms(finished)
    row.ended_at = row.finished_at
    row.auto_ended = state["endedBy"] == "abandoned"
    row.last_done_at = from_ms(max(reveals)) if reveals else None
    row.updated_at = now_utc()


def abandoned(state: State, key: list[str], now: int) -> State:
    """An idle game ended with no winner and the key shown (the domain's finish, minus a winner)."""
    return {
        **state,
        "status": "finished",
        "winner": None,
        "endedBy": "abandoned",
        "finishedAt": now,
        "clue": None,
        "guessesLeft": 0,
        "deadline": None,
        "pending": None,
        "undo": None,
        "solution": list(key),
        "rev": state["rev"] + 1,
        "updatedAt": now,
    }


def game_out(state: State, access: Access) -> IshaaraGameOut:
    """The public game for one account."""
    return IshaaraGameOut(
        game=state, me=MeOut(host=access.host, team=access.team), server_now=to_ms(now_utc())
    )


def not_found() -> ApiError:
    """404 for unknown, deleted, other kinds' and other people's games alike."""
    return ApiError(404, ErrorCode.NOT_FOUND, "No such game.")


def invalid_game(reason: str) -> ApiError:
    """422 `invalid_game {reason}` (create and rematch)."""
    return ApiError(
        422, ErrorCode.INVALID_GAME, f"The game failed the {reason} check.", reason=reason
    )


def id_conflict() -> ApiError:
    """409 `id_conflict`: the id already names another game."""
    return ApiError(409, ErrorCode.ID_CONFLICT, "This id is already used by another game.")


def action_error(error: dict[str, str], out: IshaaraGameOut) -> ApiError:
    """The domain's error as 403 `not_your_turn` or 422 `invalid_action {reason}`, with the game."""
    game = out.model_dump(mode="json")
    if error["code"] == "not_your_turn":
        return not_your_turn(game)
    reason = error.get("reason", "")
    return ApiError(
        422, ErrorCode.INVALID_ACTION, f"Not allowed now: {reason}.", reason=reason, game=game
    )


def not_your_turn(game: dict[str, Any]) -> ApiError:
    """403 `not_your_turn {game}`."""
    return ApiError(403, ErrorCode.NOT_YOUR_TURN, "That isn't yours to do now.", game=game)


def seat_ids(state: State) -> set[uuid.UUID]:
    """Registry ids of every seat."""
    return {uuid.UUID(p["playerId"]) for p in state["players"]}
