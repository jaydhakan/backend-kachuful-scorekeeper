"""What anyone with a link can see. Never emails, user ids or first/last names."""

import html
import os
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, ShareLink
from app.db.session import get_session_factory
from app.domain.enums import GameKind
from app.domain.time import from_ms, to_ms, to_ms_or_none
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.render import run_in_thread
from app.render.og import CardRow, render_recap
from app.repositories.games import games_repository
from app.repositories.players import players_repository
from app.repositories.share_links import share_links_repository
from app.repositories.users import users_repository
from app.schemas.games import GameSummaryOut
from app.schemas.public import LiveOut, RecapOut
from app.security.rate_limit import REACTION_LINK, REACTION_VIEWER
from app.services.achievements_read import achievements_for_game
from app.services.links import live_expired
from app.services.presenters import summary_out
from app.settings import settings

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Kachuful">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
{image}<meta name="twitter:card" content="{card}">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta http-equiv="refresh" content="0;url={target}">
</head>
<body><p><a href="{target}">Open in Kachuful</a></p></body>
</html>
"""


def _when(ms: int) -> str:
    return from_ms(ms).astimezone(settings.tz).strftime("%-d %b %Y")


def _leader(summary: GameSummaryOut) -> tuple[str, int] | None:
    top = max(summary.players, key=lambda p: p.points, default=None)
    return None if top is None else (top.name, top.points)


class PublicService:
    """Live and recap views behind share tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _shared_game(
        self, token: str, kind: str, signed_in: bool = True
    ) -> tuple[ShareLink, Game]:
        link = await share_links_repository.by_token(self.session, token)
        if link is None or link.kind != kind:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such link.")
        game = await games_repository.get(self.session, link.game_id, kind=GameKind.KACHUFUL)
        if link.revoked_at is not None or game is None or game.deleted_at is not None:
            raise ApiError(410, ErrorCode.GONE, "This link has been switched off.")
        if kind == "live" and live_expired(game):
            raise ApiError(410, ErrorCode.GONE, "This game ended; its live link has expired.")
        if kind == "live" and link.members_only and not signed_in:
            raise ApiError(401, ErrorCode.MEMBERS_ONLY, "Sign in to watch this table.")
        return link, game

    async def _summary(self, game: Game) -> GameSummaryOut:
        rows = await games_repository.summaries(self.session, [game.id], kind=GameKind.KACHUFUL)
        return summary_out(rows[game.id], public=True)

    async def live_body(self, game: Game, link: ShareLink) -> LiveOut:
        """The live view of a game."""
        owner = await users_repository.get(self.session, game.owner_id)
        recap = await share_links_repository.active(self.session, game.id, "recap")
        return LiveOut(
            game=game.payload,
            summary=await self._summary(game),
            updated_at=to_ms(game.updated_at),
            scorer_seen_at=None if owner is None else to_ms_or_none(owner.last_seen_at),
            reactions=link.reactions and game.ended_at is None,
            recap_token=None if recap is None else recap.token,
        )

    async def live(self, token: str, signed_in: bool = True) -> tuple[uuid.UUID, LiveOut]:
        """`GET /api/public/live/{token}`."""
        link, game = await self._shared_game(token, "live", signed_in)
        return game.id, await self.live_body(game, link)

    async def react(self, token: str, kind: str, viewer: str, signed_in: bool) -> None:
        """A spectator's reaction, sent to everyone following the table; never stored."""
        link, game = await self._shared_game(token, "live", signed_in)
        if not link.reactions or game.ended_at is not None:
            raise ApiError(409, ErrorCode.REACTIONS_OFF, "Reactions are off for this table.")
        if not REACTION_VIEWER.hit(f"{token}:{viewer}") or not REACTION_LINK.hit(token):
            raise ApiError(429, ErrorCode.RATE_LIMITED, "Easy there.", retryAfter=1)
        await notify(self.session, game.id, "reaction", data={"kind": kind})
        await self.session.commit()

    async def recap(self, token: str) -> RecapOut:
        """`GET /api/public/recap/{token}`."""
        _, game = await self._shared_game(token, "recap")
        return RecapOut(
            game=game.payload,
            summary=await self._summary(game),
            achievements=await achievements_for_game(self.session, game.id),
        )

    async def og_png(self, token: str) -> bytes:
        """The recap card, cached on disk by token, game rev and the photos it shows."""
        _, game = await self._shared_game(token, "recap")
        summary = await self._summary(game)
        ranked = sorted(summary.players, key=lambda p: -p.points)[:3]
        photos = "-".join((p.photo_url or "").rsplit("=", 1)[-1] for p in ranked)
        path = Path(settings.OG_CACHE_DIR) / f"{token}.{game.rev}.{photos or 'x'}.png"
        cached = await run_in_thread(_read_if_exists, path)
        if cached is not None:
            return cached
        rows = [
            CardRow(
                place=p.place or i + 1,
                emoji=p.emoji,
                name=p.name,
                points=p.points,
                photo=await self._photo_of(p.player_id, p.photo_url),
            )
            for i, p in enumerate(ranked)
        ]
        stamp = summary.ended_at or to_ms(game.client_updated_at)
        subtitle = f"{summary.rounds} rounds · {len(summary.players)} players · {_when(stamp)}"
        png = await run_in_thread(render_recap, "Kachuful recap", rows, subtitle)
        await run_in_thread(_write_atomically, path, png)
        return png

    async def _photo_of(self, player_id: str | None, url: str | None) -> bytes | None:
        if player_id is None or url is None:
            return None
        return await players_repository.photo(self.session, uuid.UUID(player_id))

    async def preview_page(self, kind: str, token: str) -> tuple[int, str]:
        """The tiny HTML page WhatsApp reads for a rich preview; it redirects into the app."""
        query = "recap" if kind == "recap" else "live"
        path = "r" if kind == "recap" else "l"
        target = f"/?{query}={html.escape(token)}"
        try:
            _, game = await self._shared_game(token, kind)
        except ApiError as error:
            return error.status, _render_page("Kachuful", "Scorekeeper for Judgement", "/", "", "")
        summary = await self._summary(game)
        leader = _leader(summary)
        names = ", ".join(p.name for p in summary.players)
        if kind == "recap":
            verb = "won" if summary.ended_at is not None else "leads"
            title = f"{leader[0]} {verb} with {leader[1]}" if leader else "Kachuful recap"
            description = f"{names} · {summary.rounds} rounds"
            image = f"{settings.APP_ORIGIN}/api/public/recap/{token}/og.png"
        else:
            title = f"Live: {leader[0]} leads with {leader[1]}" if leader else "Kachuful live"
            description = f"Watch {names} play, live."
            image = ""
        url = f"{settings.APP_ORIGIN}/{path}/{token}"
        return 200, _render_page(title, description, target, url, image)


def _render_page(title: str, description: str, target: str, url: str, image: str) -> str:
    image_tags = (
        f'<meta property="og:image" content="{html.escape(image)}">\n'
        '<meta property="og:image:width" content="1200">\n'
        '<meta property="og:image:height" content="630">\n'
        if image
        else ""
    )
    return _PAGE.format(
        title=html.escape(title),
        description=html.escape(description),
        url=html.escape(url),
        target=target,
        image=image_tags,
        card="summary_large_image" if image else "summary",
    )


def _read_if_exists(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


async def load_live_event(game_id: uuid.UUID) -> tuple[int, str] | None:
    """The hub's loader: the game's live JSON, if it still has an active live link."""
    async with get_session_factory()() as session:
        link = await share_links_repository.active(session, game_id, "live")
        game = await games_repository.get(session, game_id, kind=GameKind.KACHUFUL)
        if link is None or game is None or game.deleted_at is not None or live_expired(game):
            return None
        body = await PublicService(session).live_body(game, link)
        return game.rev, body.model_dump_json(by_alias=True)
