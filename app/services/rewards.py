"""What a finished-game change pays out beyond Sikke: titles, bounties and milestones, every game.

Crowns go first: a bounty reads the Taj's reigns, which the write may have just closed. The
write's status (what it moved) goes to every seated account's stream as a `status` event.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.crowns import CrownChange
from app.domain.enums import ProgressionGame
from app.domain.milestones import Milestone
from app.domain.sikke import SikkeEarned
from app.live.hub import notify_user
from app.repositories.users import users_repository
from app.schemas.crowns import StatusOut
from app.services.crowns import CrownsService
from app.services.milestones import MilestonesService
from app.services.sikke import SikkeService


@dataclass(frozen=True)
class Rewards:
    """Titles that changed hands, bounties newly paid and milestones created by one write."""

    changes: list[CrownChange] = field(default_factory=list)
    bounties: list[SikkeEarned] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)


async def reward_game(
    session: AsyncSession, game: ProgressionGame, game_id: uuid.UUID, *, void: bool
) -> Rewards:
    """Recompute one game's titles and bounties, and the milestones, after its finished games
    changed. Returns what moved for this game (bounties and milestones of other games left out)."""
    changes = await CrownsService(session).recompute(game, game_id=None if void else game_id)
    bounties = await SikkeService(session).recompute_bounties(game)
    milestones = await MilestonesService(session).recompute()
    mine = str(game_id)
    return Rewards(
        changes=changes,
        bounties=[b for b in bounties if b.game_id == mine],
        milestones=[m for m in milestones if m.game_id == mine],
    )


async def rewards_status(session: AsyncSession, rewards: Rewards) -> StatusOut:
    """A write's rewards on the wire (no query when nothing moved)."""
    return await CrownsService(session).status(
        rewards.changes, rewards.bounties, rewards.milestones
    )


async def tell_status(
    session: AsyncSession,
    game: ProgressionGame,
    game_id: uuid.UUID,
    players: set[uuid.UUID],
    status: StatusOut,
) -> None:
    """Send a write's status to every account seated in the game (when a title or bounty moved)."""
    if not status.changes and not status.bounties:
        return
    frame = {
        "type": "status",
        "game": game.value,
        "gameId": str(game_id),
        # Null fields left out, exactly as the PUT's response sends the same status.
        **status.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    for user_id in await users_repository.ids_for_players(session, players):
        await notify_user(session, user_id, "status", frame)
