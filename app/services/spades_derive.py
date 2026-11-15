"""The domain's Spades player rows as `spades_game_players` insert rows (both kinds)."""

import uuid
from datetime import datetime
from typing import Any

from app.domain.spades.stats import SpPlayerRow


def game_player_dicts(
    game_id: uuid.UUID,
    kind: str,
    rows: list[SpPlayerRow],
    finished_at: datetime,
    ended_by: str,
) -> list[dict[str, Any]]:
    """One insert row per player of a finished game; `ended_by` is the result's endedBy."""
    return [
        {
            "game_id": game_id,
            "player_id": uuid.UUID(r.player_id),
            "kind": kind,
            "seat": r.seat,
            "team": r.team,
            "partner_id": None if r.partner_id is None else uuid.UUID(r.partner_id),
            "result": r.result,
            "ended_by": ended_by,
            "score": r.score,
            "opp_score": r.opp_score,
            "hands": r.tally.hands,
            "bids": r.tally.bids,
            "bids_made": r.tally.bids_made,
            "nils": r.tally.nils,
            "nils_made": r.tally.nils_made,
            "blind_nils": r.tally.blind_nils,
            "blind_nils_made": r.tally.blind_nils_made,
            "bags": r.tally.bags,
            "sets": r.tally.sets,
            "finished_at": finished_at,
        }
        for r in rows
    ]
