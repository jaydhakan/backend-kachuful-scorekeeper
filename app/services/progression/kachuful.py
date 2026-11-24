"""Kachuful through the per-game protocol: today's rules (`domain.achievements`, `domain.awards`).

Uploads keep the engine's incremental `game`/`career` path (the PUT budget); this class serves the
recompute job, merges, ladders and daily awards.
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.achievements import (
    CAREER_CODES,
    GAME_SCOPED_CODES,
    CareerGame,
    career_from_games,
    round_milestones,
)
from app.domain.awards import daily_awards
from app.domain.enums import GameKind, ProgressionGame
from app.domain.game import game_from_payload
from app.domain.progression import Award, Earned, PlayedGame, shared_ladders
from app.domain.time import day_range_ms, now_utc, to_ms
from app.repositories.achievements import achievements_repository
from app.repositories.boards import boards_repository
from app.repositories.games import games_repository
from app.services.achievements import kachuful_game_earned
from app.services.boards import make_window
from app.services.games import derive, game_info
from app.settings import settings

LADDER_IDS = ("hot", "cold", "wins", "games", "rounds", "made", "points", "crew")


def _played(games: list[CareerGame]) -> list[PlayedGame]:
    return [PlayedGame(g.player_id, g.game_id, g.ended_at, g.won, g.others) for g in games]


class KachufulProgression:
    """Kachuful's achievements, ladders and daily awards."""

    game = ProgressionGame.KACHUFUL
    game_codes = GAME_SCOPED_CODES
    career_codes = CAREER_CODES
    ladder_ids = LADDER_IDS

    async def game_earned(self, session: AsyncSession, game_id: uuid.UUID) -> list[Earned]:
        """Round moments and game finishes, re-derived from the stored payload."""
        row = await games_repository.get(session, game_id, kind=GameKind.KACHUFUL)
        if row is None or row.deleted_at is not None:
            return []
        derived = derive(game_from_payload(row.payload), to_ms(now_utc()))
        return await kachuful_game_earned(
            session, derived.rounds, derived.players, game_info(derived)
        )

    async def career_earned(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> list[Earned]:
        """Game-sequence careers plus round milestones (one milestone query per player)."""
        games = await achievements_repository.career_games(session, player_ids)
        out = [
            e for pid in player_ids for e in career_from_games(str(pid), games.get(str(pid), []))
        ]
        for pid in sorted(player_ids):
            out += round_milestones(await achievements_repository.milestone_rounds(session, pid))
        return out

    async def ladder_counts(self, session: AsyncSession, player_id: uuid.UUID) -> dict[str, int]:
        """The app's `ladderCounts` from player_day and the ended games (two queries)."""
        rounds, made, positive, hot, cold = await achievements_repository.ladder_totals(
            session, player_id
        )
        games = await achievements_repository.career_games(session, [player_id])
        careers = shared_ladders(str(player_id), _played(games.get(str(player_id), [])))
        counts = {"hot": hot, "cold": cold, "rounds": rounds, "made": made, "points": positive}
        merged = counts | careers
        return {ladder: merged[ladder] for ladder in LADDER_IDS}

    async def daily_awards(self, session: AsyncSession, day: date) -> list[Award]:
        """The day's awards from its rounds (two queries)."""
        window = make_window(*day_range_ms(day, settings.tz))
        rounds = await boards_repository.day_rounds(session, window)
        tallies = await boards_repository.tallies(session, window)
        return daily_awards(rounds, tallies.values())
