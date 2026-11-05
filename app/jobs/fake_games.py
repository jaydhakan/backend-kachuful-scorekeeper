"""Valid, random-but-reproducible game payloads (seed jobs and tests), built with the real rules."""

import random
import uuid
from typing import Any

from app.domain.rules import TRUMPS, cards_for, cycle_len, max_for

MINUTE = 60_000


def _round(
    index: int,
    n: int,
    max_cards: int,
    first_dealer: int,
    rng: random.Random,
    done_at: int | None,
    order: str = "up",
) -> dict[str, Any]:
    cards = cards_for(index, max_cards, order)
    dealer = (first_dealer + index) % n
    ids = [f"p{k}" for k in range(n)]
    while True:
        tricks = [0] * n
        for _ in range(cards):
            tricks[rng.randrange(n)] += 1
        bids = [
            t if rng.random() < 0.5 else (t + 1 + rng.randrange(cards)) % (cards + 1)
            for t in tricks
        ]
        if sum(bids) != cards:
            break
    exact = rng.random() < 0.5
    return {
        "index": index,
        "cards": cards,
        "trump": TRUMPS[index % 4],
        "dealerIndex": dealer,
        "firstPlayerIndex": (dealer + 1) % n,
        "bids": dict(zip(ids, bids, strict=True)),
        "results": {
            pid: "made" if b == t else "missed" for pid, b, t in zip(ids, bids, tricks, strict=True)
        },
        "tricks": dict(zip(ids, tricks, strict=True)) if exact else None,
        "hookHits": 0,
        "phase": "done",
        "doneAt": done_at,
    }


def make_payload(
    player_ids: list[str],
    *,
    rounds: int = 4,
    finished: bool = True,
    start_ms: int,
    seed: int = 1,
    decks: int = 1,
    first_dealer: int = 0,
    length_mode: str = "endless",
    game_id: str | None = None,
    rev: int = 1,
    step_ms: int = MINUTE,
    open_last_round: bool = False,
    order: str = "up",
) -> dict[str, Any]:
    """A valid game. `open_last_round` adds an unfinished round being bid on."""
    rng = random.Random(seed)
    n = len(player_ids)
    max_cards = max_for(n, decks)
    if length_mode != "endless":
        rounds = min(rounds, cycle_len(max_cards) * (1 if length_mode == "1cycle" else 2))
    played = [
        _round(i, n, max_cards, first_dealer, rng, start_ms + (i + 1) * step_ms, order)
        for i in range(rounds)
    ]
    if open_last_round and not finished:
        open_round = _round(rounds, n, max_cards, first_dealer, rng, None, order)
        open_round.update(phase="bidding", bids={}, results={}, tricks=None)
        played.append(open_round)
    updated = start_ms + max(rounds, 1) * step_ms
    sid = game_id or str(uuid.uuid4())
    return {
        "id": f"g-{sid}",
        "syncId": sid,
        "rev": rev,
        "schemaVersion": 3,
        "createdAt": start_ms,
        "updatedAt": updated,
        "finishedAt": updated if finished else None,
        "status": "finished" if finished else "active",
        "direction": "cw",
        "firstDealerIndex": first_dealer,
        "decks": decks,
        "maxCards": max_cards,
        "lengthMode": length_mode,
        "order": order,
        "players": [
            {"id": f"p{k}", "playerId": pid, "name": f"P{k}", "emoji": "🙂", "color": "#123456"}
            for k, pid in enumerate(player_ids)
        ],
        "rounds": played,
        "ui": {"view": "round", "bidIdx": 0},
    }
