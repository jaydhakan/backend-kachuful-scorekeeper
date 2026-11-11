"""Kaali Teeri test games: the app's golden fixtures, seated with real registry players."""

import copy
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player
from app.domain.time import from_ms
from app.services import kt_boards, kt_games
from tests.domain.golden import load
from tests.factories import make_player

KT_NOW = 1_796_995_800_000  # 2026-12-11T13:30Z: when the app evaluated the fixtures

Registry = dict[str, Player]


def freeze_clock(monkeypatch: pytest.MonkeyPatch, ms: int = KT_NOW) -> None:
    """The fixtures were checked at KT_NOW; uploads and boards run at that time too."""
    monkeypatch.setattr(kt_games, "now_utc", lambda: from_ms(ms))
    monkeypatch.setattr(kt_boards, "now_utc", lambda: from_ms(ms))


async def seat_registry(session: AsyncSession, keys: set[str], registry: Registry) -> Registry:
    """A registry player per fixture key ("ana"), reusing those already made."""
    for key in sorted(keys - set(registry)):
        registry[key] = await make_player(session, name=f"Kt-{key}")
    return registry


def seated(game: dict[str, Any], registry: Registry) -> dict[str, Any]:
    """A copy of a fixture game whose seats carry the registry players' ids."""
    payload = copy.deepcopy(game)
    for seat in payload["players"]:
        seat["playerId"] = str(registry[seat["playerId"]].id)
    return payload


async def fixture_payload(
    session: AsyncSession, name: str, registry: Registry | None = None
) -> tuple[dict[str, Any], Registry]:
    """A fixture's game, ready to upload, and the registry used."""
    game = load(name)["game"]
    registry = await seat_registry(
        session, {s["playerId"] for s in game["players"]}, registry if registry is not None else {}
    )
    return seated(game, registry), registry


def key_of(registry: Registry) -> dict[str, str]:
    """Registry id → fixture key, for comparing stored rows with the fixtures."""
    return {str(p.id): key for key, p in registry.items()}


def new_id(payload: dict[str, Any]) -> dict[str, Any]:
    """The same game under a fresh sync id."""
    return copy.deepcopy(payload) | {"syncId": str(uuid.uuid4())}
