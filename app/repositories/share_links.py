"""Live and recap links."""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ShareLink


class ShareLinksRepository:
    """Data access for `share_links`."""

    async def active(
        self, session: AsyncSession, game_id: uuid.UUID, kind: str
    ) -> ShareLink | None:
        """A game's active link of one kind."""
        stmt = select(ShareLink).where(
            ShareLink.game_id == game_id, ShareLink.kind == kind, ShareLink.revoked_at.is_(None)
        )
        return (await session.execute(stmt)).scalars().first()

    async def by_token(self, session: AsyncSession, token: str) -> ShareLink | None:
        """A link by token, revoked or not."""
        return await session.get(ShareLink, token)

    async def create(
        self,
        session: AsyncSession,
        *,
        token: str,
        game_id: uuid.UUID,
        kind: str,
        created_by: uuid.UUID,
    ) -> ShareLink:
        """Insert a link."""
        link = ShareLink(token=token, game_id=game_id, kind=kind, created_by=created_by)
        session.add(link)
        await session.flush()
        await session.refresh(link)
        return link

    async def revoke(
        self, session: AsyncSession, game_id: uuid.UUID, kinds: tuple[str, ...]
    ) -> list[str]:
        """Revoke a game's active links of these kinds; returns their tokens."""
        stmt = (
            update(ShareLink)
            .where(
                ShareLink.game_id == game_id,
                ShareLink.kind.in_(kinds),
                ShareLink.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .returning(ShareLink.token)
        )
        return list((await session.execute(stmt)).scalars())

    async def revoked_tokens(self, session: AsyncSession) -> list[str]:
        """Every revoked token (prune removes their cached images)."""
        stmt = select(ShareLink.token).where(ShareLink.revoked_at.is_not(None))
        return list((await session.execute(stmt)).scalars())


share_links_repository = ShareLinksRepository()
