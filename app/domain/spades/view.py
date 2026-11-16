"""What one viewer receives (app: view.ts): the public table plus their own hand once they may see it."""

from typing import Any

from app.domain.spades.types import Hands, Seat, State


def can_see(s: State, seat: Seat | None) -> bool:
    """In play and looked (with Blind Nil off, everyone has looked at the deal)."""
    return seat is not None and s["status"] == "playing" and bool(s["looked"][seat])


def view_for(state: State, hands: Hands | None, seat: Seat | None, now: int) -> dict[str, Any]:
    """The table for one viewer; a Blind Nil declared ahead of its turn shows only to its own seat."""
    blind, bids = state["blind"], state["bids"]
    hidden = any(b and i != seat and bids[i] is None for i, b in enumerate(blind))
    table = state
    if hidden:
        shown = [bool(b and (i == seat or bids[i] is not None)) for i, b in enumerate(blind)]
        table = {**state, "blind": shown}
    hand = (
        list(hands[seat])
        if hands is not None and seat is not None and can_see(state, seat)
        else None
    )
    me = {"seat": seat, "host": seat is not None and seat == state["host"], "hand": hand}
    return {"table": table, "me": me, "serverNow": now}
