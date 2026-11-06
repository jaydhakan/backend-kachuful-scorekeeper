"""Creating, revoking and drawing live and recap links."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, ShareLink, User
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.logger import get_logger
from app.render import run_in_thread
from app.render.qr import qr_svg
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.repositories.share_links import share_links_repository
from app.schemas.public import LinkOut, LinkSettingsIn
from app.security.tokens import new_share_token
from app.settings import settings

logger = get_logger(__name__)

PATHS = {"live": "l", "recap": "r"}
# A live table stays watchable this long after the game ends, then the recap takes over.
LIVE_GRACE = timedelta(hours=2)


def live_expires_at(ended_at: datetime | None) -> datetime | None:
    """When a live link of a game stops working; None while the game goes on."""
    return None if ended_at is None else ended_at + LIVE_GRACE


def live_expired(game: Game) -> bool:
    """The game ended more than `LIVE_GRACE` ago."""
    end = live_expires_at(game.ended_at)
    return end is not None and now_utc() >= end


def link_url(kind: str, token: str) -> str:
    """The link people open (served by nginx → /r or /l preview pages)."""
    return f"{settings.APP_ORIGIN}/{PATHS[kind]}/{token}"


def _link_out(link: ShareLink, game: Game | None = None) -> LinkOut:
    end = live_expires_at(game.ended_at) if game is not None and link.kind == "live" else None
    return LinkOut(
        token=link.token,
        url=link_url(link.kind, link.token),
        qr_svg_url=f"{settings.APP_ORIGIN}/api/games/{link.game_id}/links/{link.kind}/qr.svg",
        reactions=link.reactions,
        members_only=link.members_only,
        expires_at=None if end is None else to_ms(end),
    )


class LinksService:
    """`/api/games/{id}/links`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _own_game(self, game_id: uuid.UUID, user: User) -> Game:
        row = await games_repository.get(self.session, game_id)
        if row is None or row.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can share it.")
        return row

    async def create(self, game_id: uuid.UUID, kind: str, user: User) -> LinkOut:
        """The game's active link of this kind, made if there isn't one."""
        game = await self._own_game(game_id, user)
        await games_repository.lock(self.session, game_id)
        link = await share_links_repository.active(self.session, game_id, kind)
        # An expired live link is as good as revoked: a new one is made.
        if link is not None and kind == "live" and live_expired(game):
            await share_links_repository.revoke(self.session, game_id, ("live",))
            link = None
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
        return _link_out(link, game)

    async def update_live(self, game_id: uuid.UUID, body: LinkSettingsIn, user: User) -> LinkOut:
        """Switch reactions or members-only on the active live link; viewers get the change."""
        game = await self._own_game(game_id, user)
        link = await share_links_repository.active(self.session, game_id, "live")
        if link is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No active live link.")
        if body.reactions is not None:
            link.reactions = body.reactions
        if body.members_only is not None:
            link.members_only = body.members_only
        await self.session.flush()
        await notify(self.session, game_id, "game")
        await self.session.commit()
        return _link_out(link, game)

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
