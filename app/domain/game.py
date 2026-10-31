"""The game as the domain sees it (app: src/domain/types.ts). Built from a structurally valid payload."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Seat:
    """One player at the table. `id` is game-local; `player_id` is the registry id."""

    id: str
    player_id: str | None
    name: str
    emoji: str
    color: str


@dataclass(frozen=True)
class Round:
    """One deal. Bids, results and tricks are keyed by seat id and kept raw for validation."""

    index: Any
    cards: Any
    trump: Any
    dealer_index: Any
    first_player_index: Any
    bids: Mapping[str, Any]
    results: Mapping[str, Any]
    tricks: Mapping[str, Any] | None
    hook_hits: int
    phase: str
    done_at: int | None

    @property
    def done(self) -> bool:
        """Confirmed rounds are the only ones that score."""
        return self.phase == "done"


@dataclass(frozen=True)
class Game:
    """A scored game. `id` is the stable sync id (the app's `syncId`, else `id`)."""

    id: str
    rev: int
    created_at: int
    updated_at: int
    finished_at: int | None
    status: str
    first_dealer_index: int
    decks: Any
    max_cards: int
    length_mode: str
    players: tuple[Seat, ...]
    rounds: tuple[Round, ...]

    @property
    def done_rounds(self) -> tuple[Round, ...]:
        """Rounds that count."""
        return tuple(r for r in self.rounds if r.done)


def sync_id(payload: Mapping[str, Any]) -> str:
    """The id a game is stored under: `syncId` when the app sends one, else `id`."""
    return str(payload.get("syncId") or payload["id"])


def _seat(raw: Mapping[str, Any]) -> Seat:
    return Seat(
        id=str(raw["id"]),
        player_id=None if raw.get("playerId") is None else str(raw["playerId"]),
        name=str(raw["name"]),
        emoji=str(raw.get("emoji", "")),
        color=str(raw.get("color", "")),
    )


def _round(raw: Mapping[str, Any]) -> Round:
    return Round(
        index=raw.get("index"),
        cards=raw.get("cards"),
        trump=raw.get("trump"),
        dealer_index=raw.get("dealerIndex"),
        first_player_index=raw.get("firstPlayerIndex"),
        bids=raw.get("bids") or {},
        results=raw.get("results") or {},
        tricks=raw.get("tricks"),
        hook_hits=int(raw.get("hookHits") or 0),
        phase=str(raw.get("phase")),
        done_at=raw.get("doneAt"),
    )


def game_from_payload(payload: Mapping[str, Any]) -> Game:
    """A Game from the app's JSON (already checked for shape by the API layer)."""
    return Game(
        id=sync_id(payload),
        rev=int(payload.get("rev", 0)),
        created_at=int(payload["createdAt"]),
        updated_at=int(payload["updatedAt"]),
        finished_at=payload.get("finishedAt"),
        status=str(payload["status"]),
        first_dealer_index=int(payload["firstDealerIndex"]),
        decks=payload.get("decks", 1),
        max_cards=int(payload["maxCards"]),
        length_mode=str(payload["lengthMode"]),
        players=tuple(_seat(p) for p in payload["players"]),
        rounds=tuple(_round(r) for r in payload["rounds"]),
    )
