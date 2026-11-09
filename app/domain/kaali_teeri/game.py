"""The Kaali Teeri game as the domain sees it (app: types.ts). Values stay raw so validation can judge them."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class _Missing:
    """A key the payload did not have: JavaScript's `undefined`, unlike JSON `null`."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


class Sweep(StrEnum):
    """Which side took every point of a hand."""

    NONE = "none"
    BIDDERS = "bidders"
    DEFENDERS = "defenders"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def same(a: Any, b: Any) -> bool:
    """JavaScript's `===` for JSON values: 1 === 1.0, but true !== 1 and objects only by identity."""
    if _is_number(a) and _is_number(b):
        return bool(a == b)
    if isinstance(a, bool | str) and type(a) is type(b):
        return bool(a == b)
    return a is b


def includes(values: Any, value: Any) -> bool:
    """`Array.prototype.includes` with `===` (NaN never appears in JSON)."""
    return any(same(v, value) for v in values)


def js_number(value: Any) -> float:
    """What JavaScript's `<`/`>` turn a JSON value into: null → 0, junk → NaN."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


@dataclass(frozen=True)
class KtSeat:
    """One player at the table: game-local `id`, registry `player_id`."""

    id: str
    player_id: str | None
    name: str
    emoji: str
    color: str


@dataclass(frozen=True)
class KtRules:
    """The rules stored in the game, raw until `rules_ok` says they fit the table."""

    decks: Any
    partners: Any
    min_bid: Any
    step: Any
    length: Any


@dataclass(frozen=True)
class KtCard:
    """A called card; `copy` is MISSING when the payload has none."""

    rank: Any
    suit: Any
    copy: Any = MISSING


@dataclass(frozen=True)
class KtCall:
    """One auction call. `keys` keeps which of bid/pass/forced the payload had (TS `in`)."""

    player_id: Any
    keys: frozenset[str]
    bid: Any = MISSING
    forced: Any = MISSING


@dataclass(frozen=True)
class KtHand:
    """One hand; every field is raw (MISSING when absent)."""

    index: Any
    dealer_index: Any
    phase: Any
    mode: Any
    calls: tuple[KtCall, ...]
    bidder: Any
    bid: Any
    trump: Any
    called: tuple[KtCard, ...]
    played_by: tuple[Any, ...]
    team_points: Any
    counted_by: Any
    done_at: Any

    @property
    def done(self) -> bool:
        """Only done hands score."""
        return same(self.phase, "done")


@dataclass(frozen=True)
class KtGame:
    """A Kaali Teeri game; `id` is the sync id it is stored under."""

    id: str
    rev: int
    created_at: int
    updated_at: int
    finished_at: int | None
    status: str
    players: tuple[KtSeat, ...]
    first_dealer_index: Any
    direction: Any
    rules: KtRules
    hands: tuple[KtHand, ...]

    @property
    def done_hands(self) -> tuple[KtHand, ...]:
        """Hands that count."""
        return tuple(h for h in self.hands if h.done)


def _seat(raw: Mapping[str, Any]) -> KtSeat:
    player_id = raw.get("playerId")
    return KtSeat(
        id=str(raw["id"]),
        player_id=None if player_id is None else str(player_id),
        name=str(raw["name"]),
        emoji=str(raw.get("emoji", "")),
        color=str(raw.get("color", "")),
    )


def _rules(raw: Any) -> KtRules:
    r: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    return KtRules(
        decks=r.get("decks", MISSING),
        partners=r.get("partners", MISSING),
        min_bid=r.get("minBid", MISSING),
        step=r.get("step", MISSING),
        length=r.get("length", MISSING),
    )


def _call(raw: Mapping[str, Any]) -> KtCall:
    return KtCall(
        player_id=raw.get("playerId", MISSING),
        keys=frozenset(k for k in ("bid", "pass", "forced") if k in raw),
        bid=raw.get("bid", MISSING),
        forced=raw.get("forced", MISSING),
    )


def _card(raw: Mapping[str, Any]) -> KtCard:
    return KtCard(
        rank=raw.get("rank", MISSING), suit=raw.get("suit", MISSING), copy=raw.get("copy", MISSING)
    )


def _hand(raw: Mapping[str, Any]) -> KtHand:
    return KtHand(
        index=raw.get("index", MISSING),
        dealer_index=raw.get("dealerIndex", MISSING),
        phase=raw.get("phase", MISSING),
        mode=raw.get("mode", MISSING),
        calls=tuple(_call(c) for c in raw.get("calls") or ()),
        bidder=raw.get("bidder", MISSING),
        bid=raw.get("bid", MISSING),
        trump=raw.get("trump", MISSING),
        called=tuple(_card(c) for c in raw.get("called") or ()),
        played_by=tuple(raw.get("playedBy") or ()),
        team_points=raw.get("teamPoints", MISSING),
        counted_by=raw.get("countedBy", MISSING),
        done_at=raw.get("doneAt", MISSING),
    )


def kt_game_from_payload(payload: Mapping[str, Any]) -> KtGame:
    """A KtGame from the app's JSON (already checked for shape by the API layer)."""
    return KtGame(
        id=str(payload["syncId"]),
        rev=int(payload.get("rev", 0)),
        created_at=int(payload["createdAt"]),
        updated_at=int(payload["updatedAt"]),
        finished_at=payload.get("finishedAt"),
        status=str(payload["status"]),
        players=tuple(_seat(p) for p in payload["players"]),
        first_dealer_index=payload.get("firstDealerIndex", MISSING),
        direction=payload.get("direction", MISSING),
        rules=_rules(payload.get("rules")),
        hands=tuple(_hand(h) for h in payload.get("hands") or ()),
    )
