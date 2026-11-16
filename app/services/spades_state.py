"""Spades online state ⇄ the `games` row: columns, summary, views and errors. No I/O.

Shared by the tables service, its seat invites, the live hub's frames, the public links and the
boards' game lists.
"""

import uuid
from typing import Any

from app.db.models import Game
from app.domain.spades import scores_of, totals, view_for
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.schemas.spades_tables import SpTableSummaryOut, SpViewOut
from app.services.games import payload_hash

State = dict[str, Any]
Invites = list[dict[str, Any] | None]
NO_INVITES: Invites = [None, None, None, None]


def summary_of(state: State) -> dict[str, Any]:
    """The stored `games.summary`: public fields only; seats by player, never by account."""
    return {
        "code": state["code"],
        "name": state["name"],
        "status": state["status"],
        "phase": state["phase"],
        "paused": state["paused"],
        "rules": state["rules"],
        "host": state["host"],
        "seats": [_summary_seat(s["who"]) for s in state["seats"]],
        "scores": scores_of(totals(state["history"])),
        "hands": len(state["history"]),
        "result": state["result"],
        "createdAt": state["createdAt"],
        "updatedAt": state["updatedAt"],
        "finishedAt": state.get("finishedAt"),
    }


def _summary_seat(who: dict[str, Any] | None) -> dict[str, Any] | None:
    if who is None:
        return None
    return {k: who.get(k) for k in ("playerId", "name", "emoji", "color", "photoUrl")}


def _last_done(state: State) -> int | None:
    done = [h["doneAt"] for h in state["history"] if isinstance(h.get("doneAt"), int)]
    return max(done) if done else None


def apply_state(row: Game, state: State) -> None:
    """Write a state into its row: payload, rev, status, summary and the columns jobs filter on.

    Cancelled tables are `finished` rows (the games CHECK has no cancelled); their summary says so.
    """
    finished = state.get("finishedAt")
    last = _last_done(state)
    row.rev = state["rev"]
    row.payload = state
    row.payload_hash = payload_hash(state)
    row.schema_version = state["schemaVersion"]
    row.status = "active" if state["status"] in ("lobby", "playing") else "finished"
    row.player_count = sum(1 for s in state["seats"] if s["who"] is not None)
    row.round_count = len(state["history"])
    row.summary = summary_of(state)
    row.created_at = from_ms(state["createdAt"])
    row.client_updated_at = from_ms(state["updatedAt"])
    row.finished_at = None if finished is None else from_ms(finished)
    row.ended_at = row.finished_at
    row.auto_ended = False
    row.last_done_at = None if last is None else from_ms(last)
    row.updated_at = now_utc()


def seat_of_user(state: State, user_id: uuid.UUID) -> int | None:
    """The account's seat at the table now, or None."""
    for i, seat in enumerate(state["seats"]):
        who = seat["who"]
        if who is not None and who.get("userId") == str(user_id):
            return i
    return None


def view_body(
    state: State, hands: list[list[dict[str, Any]]] | None, seat: int | None, invites: Invites
) -> dict[str, Any]:
    """SpView as JSON-ready camelCase: only `viewFor`'s output, plus the seats' invites."""
    return {**view_for(state, hands, seat, to_ms(now_utc())), "invites": invites}


def view_out(
    state: State, hands: list[list[dict[str, Any]]] | None, seat: int | None, invites: Invites
) -> SpViewOut:
    """SpView for a response."""
    return SpViewOut.model_validate(view_body(state, hands, seat, invites))


def public_table(state: State) -> State:
    """What a share link shows: the table no seat sees, with account ids taken out."""
    table: State = view_for(state, None, None, 0)["table"]
    seats = [
        {**s, "who": None if s["who"] is None else {**s["who"], "userId": ""}}
        for s in table["seats"]
    ]
    return {**table, "seats": seats}


def table_summary_out(
    game_id: uuid.UUID,
    summary: dict[str, Any],
    *,
    player_id: uuid.UUID | None,
    is_host: bool,
    updated_at: int | None = None,
) -> SpTableSummaryOut:
    """A table summary for a list; `player_id` finds `mySeat` (the caller's player, or None)."""
    mine = None if player_id is None else str(player_id)
    seats = summary["seats"]
    my_seat = next((i for i, s in enumerate(seats) if s and s["playerId"] == mine), None)
    return SpTableSummaryOut.model_validate(
        {
            **summary,
            "id": game_id,
            "updatedAt": summary["updatedAt"] if updated_at is None else updated_at,
            "mySeat": my_seat,
            "isHost": is_host,
        }
    )


def not_found() -> ApiError:
    """404 for unknown, deleted and other kinds' tables alike."""
    return ApiError(404, ErrorCode.NOT_FOUND, "No such table.")


def refusal_error(error: dict[str, str], view: SpViewOut) -> ApiError:
    """The domain's refusal as 403 `not_your_turn` or 422 `invalid_action {reason}`, with the view."""
    game = view.model_dump(mode="json")
    if error["code"] == "not_your_turn":
        return ApiError(403, ErrorCode.NOT_YOUR_TURN, "That isn't yours to do now.", game=game)
    reason = error.get("reason", "")
    return ApiError(
        422, ErrorCode.INVALID_ACTION, f"Not allowed now: {reason}.", reason=reason, game=game
    )


def stale_error(view: SpViewOut) -> ApiError:
    """409 `stale {game}`."""
    return ApiError(
        409, ErrorCode.STALE, "The table has moved on.", game=view.model_dump(mode="json")
    )
