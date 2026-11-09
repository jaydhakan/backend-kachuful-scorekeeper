"""Deleting every game a user scored, of every kind: start fresh, admin clear and delete user.

Each kind deletes through its own single-game use case, so derived rows, links and live viewers
are handled the same way as when one game is deleted.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import GameKind
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.players import players_repository
from app.schemas.games import DeletedOut
from app.services.games import GamesService
from app.services.kt_games import KtGamesService

logger = get_logger(__name__)

DeleteOwned = Callable[[AsyncSession, User, User], Awaitable[int]]


async def _delete_kachuful(session: AsyncSession, owner: User, actor: User) -> int:
    return await GamesService(session).delete_owned(owner, actor)


async def _delete_kaali_teeri(session: AsyncSession, owner: User, actor: User) -> int:
    return await KtGamesService(session).delete_owned(owner, actor)


# One entry per kind that stores games; a kind missing here would outlive its owner's account.
DELETE_OWNED_BY_KIND: dict[GameKind, DeleteOwned] = {
    GameKind.KACHUFUL: _delete_kachuful,
    GameKind.KAALI_TEERI: _delete_kaali_teeri,
}


async def delete_owned_games(session: AsyncSession, owner: User, actor: User) -> int:
    """Soft-delete every live game `owner` scored, of every kind, as `actor`; commits per game."""
    deleted = 0
    for delete_owned in DELETE_OWNED_BY_KIND.values():
        deleted += await delete_owned(session, owner, actor)
    return deleted


class MyDataService:
    """`DELETE /api/me/data`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start_fresh(self, user: User) -> DeletedOut:
        """Delete every game the user scored, then the players they added that nothing else uses."""
        games = await delete_owned_games(self.session, user, user)
        players = await players_repository.delete_unused_created_by(self.session, user.id)
        if players:
            await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("start fresh", extra={"games": games, "players": players})
        return DeletedOut(games=games, players=players)
