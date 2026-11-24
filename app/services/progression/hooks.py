"""What a stored game's writes call: its own rows, its players' careers, its feed item.

Kachuful's uploads keep their own incremental path in `GamesService.store`; every other kind calls
`progress_game` after it has rewritten its derived rows, in the same transaction.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.enums import GAME_OF_KIND, GameKind
from app.repositories.achievements import achievements_repository
from app.schemas.crowns import StatusOut
from app.services.achievements import AchievementsEngine
from app.services.nights import NightsService
from app.services.progression.registry import progression_of_kind
from app.services.rewards import reward_game, rewards_status, tell_status
from app.services.sikke import SikkeService


def _empty_status() -> StatusOut:
    return StatusOut(changes=[], bounties=[])


@dataclass(frozen=True)
class Progressed:
    """What a write earned: the achievement rows added and what it moved (titles, bounties,
    milestones; empty lists when nothing did)."""

    earned: list[Achievement]
    status: StatusOut = field(default_factory=_empty_status)


async def progress_game(
    session: AsyncSession,
    kind: GameKind,
    game_id: uuid.UUID,
    players: set[uuid.UUID],
    *,
    finished: bool,
    void: bool = False,
    career: bool = True,
    settled: bool | None = None,
) -> Progressed:
    """Recompute one game's rows, then (unless `career` is off) its players' careers.

    `finished`: the game ended with a result, so it has a `game_finished` feed item; otherwise any
    such item goes. `void` (deleted, abandoned): the game earns nothing. With `career` on, the
    players' Sikke and any closed night of the game follow (a merge, which turns it off, runs its
    own); when the finished games may have changed (`settled`, by default `finished or void`), so
    do the game's titles and bounties and the milestones, told to the seated accounts.
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
    if not career:
        return Progressed(earned=earned)
    await SikkeService(session).recompute_players(players)
    status = _empty_status()
    if (finished or void) if settled is None else settled:
        game = GAME_OF_KIND[kind]
        status = await rewards_status(session, await reward_game(session, game, game_id, void=void))
        await tell_status(session, game, game_id, players, status)
    await NightsService(session).after_write({game_id})
    return Progressed(earned=earned, status=status)
