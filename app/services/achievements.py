"""Recomputing achievements after a write (plan §8.6). Rows that still hold keep their earned_at."""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.achievements import (
    CAREER_CODES,
    GAME_SCOPED_CODES,
    ROUND_MILESTONE_CODES,
    Earned,
    career_from_games,
    game_finishes,
    milestones_reached,
    round_milestones,
    round_moments,
)
from app.domain.derive import Facts, GameFact, GameInfo, RoundFact
from app.domain.time import to_ms
from app.repositories.achievements import achievements_repository, key_of
from app.settings import settings

SEQUENCE_CODES = CAREER_CODES - ROUND_MILESTONE_CODES


def activity_rows(rows: Sequence[Achievement]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "achievement",
            "player_id": row.player_id,
            "game_id": row.game_id,
            "achievement_id": row.id,
            "data": {"code": row.code, **row.data},
        }
        for row in rows
    ]


class AchievementsEngine:
    """Keeps the achievements table equal to what the rules say, scope by scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _apply(
        self, desired: Iterable[Earned], existing: Iterable[Achievement]
    ) -> list[Achievement]:
        wanted = {e.key: e for e in desired}
        stored = {key_of(row): row for row in existing}
        await achievements_repository.delete_ids(
            self.session, [row.id for key, row in stored.items() if key not in wanted]
        )
        inserted = await achievements_repository.insert(
            self.session, [e for key, e in wanted.items() if key not in stored]
        )
        await achievements_repository.add_activity(self.session, activity_rows(inserted))
        return inserted

    async def game(
        self,
        game_id: uuid.UUID,
        rounds: list[RoundFact],
        players: list[GameFact],
        info: GameInfo | None,
    ) -> list[Achievement]:
        """Round moments and game finishes for one game (none when `info` is None: deleted)."""
        desired: list[Earned] = []
        if info is not None:
            facts = Facts(rounds=rounds, players=players, games=[info])
            desired = round_moments(rounds) + game_finishes(facts, settings.tz)
        existing = await achievements_repository.for_game(self.session, game_id, GAME_SCOPED_CODES)
        return await self._apply(desired, existing)

    async def career(
        self,
        player_ids: set[uuid.UUID],
        *,
        changed_from_ms: int | None,
        sequence_changed: bool,
    ) -> list[Achievement]:
        """Career rows for some players. `changed_from_ms`: earliest round touched (None = all)."""
        if not player_ids:
            return []
        existing = await achievements_repository.for_players(self.session, player_ids, CAREER_CODES)
        inserted: list[Achievement] = []
        if sequence_changed:
            games = await achievements_repository.career_games(self.session, player_ids)
            desired = [
                e
                for pid in player_ids
                for e in career_from_games(str(pid), games.get(str(pid), []))
            ]
            inserted += await self._apply(
                desired, [r for r in existing if r.code in SEQUENCE_CODES]
            )
        totals = await achievements_repository.totals(self.session, player_ids)
        for pid in player_ids:
            mine = [r for r in existing if r.player_id == pid and r.code in ROUND_MILESTONE_CODES]
            reached = milestones_reached(*totals.get(str(pid), (0, 0, 0)))
            untouched = changed_from_ms is not None and all(
                to_ms(r.earned_at) < changed_from_ms for r in mine
            )
            if untouched and {r.code for r in mine} == reached:
                continue
            rows = await achievements_repository.milestone_rounds(self.session, pid)
            inserted += await self._apply(round_milestones(rows), mine)
        return inserted
