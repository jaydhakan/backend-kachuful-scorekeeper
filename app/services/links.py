"""Creating, revoking and drawing live and recap links."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ShareLink, User
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.logger import get_logger
from app.render import run_in_thread
from app.render.qr import qr_svg
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.repositories.share_links import share_links_repository
from app.schemas.public import LinkOut
from app.security.tokens import new_share_token
from app.settings import settings

logger = get_logger(__name__)

PATHS = {"live": "l", "recap": "r"}


def link_url(kind: str, token: str) -> str:
    """The link people open (served by nginx → /r or /l preview pages)."""
    return f"{settings.APP_ORIGIN}/{PATHS[kind]}/{token}"


def _link_out(link: ShareLink) -> LinkOut:
    return LinkOut(
        token=link.token,
        url=link_url(link.kind, link.token),
        qr_svg_url=f"{settings.APP_ORIGIN}/api/games/{link.game_id}/links/{link.kind}/qr.svg",
    )


class LinksService:
    """`/api/games/{id}/links`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _own_game(self, game_id: uuid.UUID, user: User) -> None:
        row = await games_repository.get(self.session, game_id)
        if row is None or row.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can share it.")

    async def create(self, game_id: uuid.UUID, kind: str, user: User) -> LinkOut:
        """The game's active link of this kind, made if there isn't one."""
        await self._own_game(game_id, user)
        await games_repository.lock(self.session, game_id)
        link = await share_links_repository.active(self.session, game_id, kind)
        if link is None:
            link = await share_links_repository.create(
                self.session,
                token=new_share_token(),
                game_id=game_id,
                kind=kind,
                created_by=user.id,
            )
            await app_state_repository.bump(self.session)
            await self.session.commit()
            logger.info("share link created", extra={"game_id": str(game_id), "kind": kind})
        return _link_out(link)

    async def revoke(self, game_id: uuid.UUID, kind: str, user: User) -> None:
        """Revoke the active link of a kind; live viewers get `revoked`."""
        await self._own_game(game_id, user)
        tokens = await share_links_repository.revoke(self.session, game_id, (kind,))
        if tokens:
            await notify(self.session, game_id, "revoked", tokens)
            await app_state_repository.bump(self.session)
        await self.session.commit()

    async def qr(self, game_id: uuid.UUID, kind: str) -> str:
        """The active link's QR code as SVG; 404 when there is none."""
        link = await share_links_repository.active(self.session, game_id, kind)
        if link is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No active link of that kind.")
        return await run_in_thread(qr_svg, link_url(kind, link.token))
