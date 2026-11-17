"""A stand-in rules module for wiring tests: every game it is swapped in for earns one moment code
per configured player, one career code per player recomputed, and one daily award."""

import uuid
from dataclasses import dataclass, field
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProgressionGame
from app.domain.progression import Award, Earned
from app.services.progression import registry

AT = 1_790_000_000_000  # 2026-09-21: when the fake's rows are earned


@dataclass
class FakeRules:
    """Records every call; earns `t_moment` (game rows) and `t_career` (careers)."""

    game: ProgressionGame
    players: list[str] = field(default_factory=list)
    game_codes: frozenset[str] = frozenset({"t_moment"})
    career_codes: frozenset[str] = frozenset({"t_career"})
    ladder_ids: tuple[str, ...] = ("t",)
    games: list[uuid.UUID] = field(default_factory=list)
    careers: list[set[uuid.UUID]] = field(default_factory=list)

    async def game_earned(self, session: AsyncSession, game_id: uuid.UUID) -> list[Earned]:
        self.games.append(game_id)
        return [Earned("t_moment", p, str(game_id), 0, None, AT) for p in self.players]

    async def career_earned(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> list[Earned]:
        self.careers.append(set(player_ids))
        return [Earned("t_career", str(p), None, None, None, AT) for p in sorted(player_ids)]

    async def ladder_counts(self, session: AsyncSession, player_id: uuid.UUID) -> dict[str, int]:
        return {"t": 1}

    async def daily_awards(self, session: AsyncSession, day: date) -> list[Award]:
        return [Award("t_day", p, 1.0) for p in self.players[:1]]


def swap_in(monkeypatch: pytest.MonkeyPatch, game: ProgressionGame) -> FakeRules:
    """Replace one game's registry entry with a fake for the test."""
    fake = FakeRules(game=game)
    monkeypatch.setitem(registry.REGISTRY, game, fake)
    return fake
