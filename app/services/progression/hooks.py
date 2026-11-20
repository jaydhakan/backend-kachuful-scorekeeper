"""What a stored game's writes call: its own rows, its players' careers, its feed item.

Kachuful's uploads keep their own incremental path in `GamesService.store`; every other kind calls
`progress_game` after it has rewritten its derived rows, in the same transaction.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.enums import GameKind
from app.repositories.achievements import achievements_repository
from app.services.achievements import AchievementsEngine
from app.services.progression.registry import progression_of_kind
from app.services.sikke import SikkeService


async def progress_game(
    session: AsyncSession,
    kind: GameKind,
    game_id: uuid.UUID,
    players: set[uuid.UUID],
    *,
    finished: bool,
    void: bool = False,
    career: bool = True,
) -> list[Achievement]:
    """Recompute one game's rows, then (unless `career` is off) its players' careers.

    `finished`: the game ended with a result, so it has a `game_finished` feed item; otherwise any
    such item goes. `void` (deleted, abandoned): the game earns nothing. Returns the rows added.
    With `career` on, the players' Sikke follows (a merge, which turns it off, runs its own).
    """
    rules = progression_of_kind(kind)
    engine = AchievementsEngine(session)
    earned = await engine.recompute_game(rules, game_id, void=void)
    if career:
        earned += await engine.recompute_careers(rules, players)
    if finished and not void:
        await achievements_repository.ensure_game_finished(session, game_id, game=rules.game)
    else:
        await achievements_repository.delete_activity(session, game_id, "game_finished")
    if career:
        await SikkeService(session).recompute_players(players)
    return earned
